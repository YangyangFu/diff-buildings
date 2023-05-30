from typing import Callable
import jax 
import jax.numpy as jnp
from jax.tree_util import Partial
import flax.linen as nn
import pandas as pd 
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from flax.training.train_state import TrainState
import optax 

from dynax.models.RC import Discrete4R3C
from dynax.models.RC import Continuous4R3C

# instantiate a model
#model = Discrete4R3C()
model = Continuous4R3C()
state_dim = model.state_dim
input_dim = model.input_dim
output_dim = model.output_dim

# ===========================================================
# Method 1 for forward simulation: jittable function
# ===========================================================
# investigate the model structure
print(model.tabulate(jax.random.PRNGKey(0), jnp.zeros((state_dim,)), jnp.zeros((input_dim,))))

# load calibration data
inputs = pd.read_csv('./disturbance_1min.csv', index_col=[0])
n_samples = len(inputs)
index = range(0, n_samples*60, 60)
inputs.index = index

# resample to a given time step
dt = 900
inputs_dt = inputs.groupby([inputs.index // dt]).mean()
u_dt = inputs_dt.values[:,:5]
y_dt = inputs_dt.values[:,5] 

# TODO: construct a data loader

# forward step with euler method
@Partial(jax.jit, static_argnums=(0,))
def forward_step(model, params, state, input, dt):
    dx, output = model.apply(params, state, input)
    new_state = state + dx*dt
    return new_state, output

def forward(model, params, state, inputs, t, dt):
    """
    Forward simulation of a given model
    """
    n_steps = len(t)
    new_state = state
    states = jnp.zeros((n_steps, state_dim))
    outputs = jnp.zeros((n_steps, output_dim))
    for i in range(n_steps):
        new_state, output = forward_step(model, params, new_state, inputs[i,:], dt)
        states = states.at[i].set(new_state)
        outputs = outputs.at[i].set(output)

    return states, outputs

# forward simulation
tsol = jnp.arange(0, len(u_dt)*dt, dt)
state = jnp.array([20., 30., 26.])  # initial state

# get an initial guess of the parameters
key = jax.random.PRNGKey(0)
params = model.init(key, state, u_dt[0,:])

# simulate the model
states, outputs = forward(model, params, state, u_dt,  tsol, dt)
print(states.shape, outputs.shape)

# train state
lr = 0.001

# we consider inverse simulation as an optimization problem
# train_state: contains the parameters, bounds, forward simulation settings, etc.
#   - step: interation step
#   - params: learnable parameters
#   - tx: optimizer
#   - apply_fn: forward simulation function
#   - params_lb: lower bound of the parameters
#   - params_ub: upper bound of the parameters

params_init = model.init(key, state, u_dt[0,:])
params_lb = params_init.unfreeze()
params_lb['params'] = {'Cai': 1.0E4, 'Cwe': 1.0E5, 'Cwi': 1.0E6, 'Re': 1.0E1, 'Ri': 1.0E1, 'Rw': 1.0E1, 'Rg': 1.0E1} # 'Twe0': 30.0, 'Twi0': 30.0
params_ub = params_init.unfreeze()
params_ub['params'] = {'Cai': 1.0E6, 'Cwe': 1.0E7, 'Cwi': 1.0E8, 'Re': 1.0E3, 'Ri': 1.0E3, 'Rw': 1.0E3, 'Rg': 1.0E3}
print(params_init)
print(params_lb['params'].values())

class InverseProblemState(TrainState):
    params_lb: nn.Module
    params_ub: nn.Module

train_state = InverseProblemState.create(
    apply_fn=forward,
    params=model.init(key, state, u_dt[0,:]),
    tx = optax.adam(learning_rate=lr),
    params_lb = params_lb,
    params_ub = params_ub
)

# =========================================================
# Method 2 for forward simulation: nn.Module
#   - need add jit to forward otherwise slow simulation
# =========================================================

# inherite from a nn.Module seems not a good idea as the parameters are hard to propogate from low-level models to high-level simulator
class Simulator(nn.Module):
    t: jnp.ndarray
    dt: float

    def setup(self):
        self.model = model

    def __call__(self, x_init, u):
        xsol = jnp.zeros((len(self.t)+1, self.model.state_dim))
        ysol = jnp.zeros((len(self.t), self.model.output_dim))

        xi = x_init
        xsol = xsol.at[0].set(xi)
        u = u.reshape(-1, self.model.input_dim)
        for i in range(len(self.t)):
            ui = u[i,:]
            xi_rhs, yi = self.model(xi, ui)
            # explicit Euler
            xi = xi + xi_rhs*self.dt

            # save results
            xsol = xsol.at[i+1].set(xi)
            ysol = ysol.at[i].set(yi)

        return xsol, ysol

simulator = Simulator(tsol, dt)
params_sim = simulator.init(jax.random.PRNGKey(0), jnp.zeros((model.state_dim,)), u_dt) 
print(params_sim)
print(simulator.tabulate(jax.random.PRNGKey(0), jnp.zeros((model.state_dim,)), u_dt))

# inverse simulation train_step
def train_step(train_state, state_init, u, t, dt, target):
    
    def mse_loss(params):
        # prediction
        outputs_pred = forward(model, params, state_init, u, t, dt)
        pred_loss = jnp.mean((outputs_pred - target)**2)

        # parameter regularization
        reg = jnp.sum(jax.nn.relu(params['params'] - train_state.params_ub['params']) + jax.nn.relu(train_state.params_lb['params'] - params['params']))
        
        return pred_loss + reg
    
    loss, grad = jax.value_and_grad(mse_loss)(train_state.params)
    train_state = train_state.apply_gradients(grads=grad)

    return loss, grad, train_state
