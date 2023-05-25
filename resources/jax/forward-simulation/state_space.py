from typing import Callable, List, Tuple, Union, Optional
from abc import ABC, abstractmethod

import jax.numpy as jnp
from flax import linen as nn
import jax 
import time 

class BaseBlockSSM(nn.Module):
    state_dim: int
    input_dim: int
    output_dim: int

    def setup(self):
        self._fxx: Optional[nn.Module] = None
        self._fxu: Optional[nn.Module] = None
        self._fyx: Optional[nn.Module] = None
        self._fyu: Optional[nn.Module] = None
        self._fx: Optional[nn.Module] = None
        self._fy: Optional[nn.Module] = None

    def __call__(self, x, u):
        # TODO: how to specify the x and u for _fx and _fy, which uses only one argument
        # combinations of dynamic equation
        if self._fxx and self._fxu:
            rhs = self._fxx(x) + self._fxu(u)
        elif self._fx:
            rhs = self._fx(x, u)
        else:
            raise NotImplementedError("dynamic equation is not implemented")
        
        # combinations of observation equation
        if self._fyx and self._fyu:
            y = self._fyx(x) + self._fyu(u)
        elif self._fy:
            y = self._fy(x, u)
        else:
            raise NotImplementedError("observation equation is not implemented")
        
        return rhs, y

# another option for linear learnable state space model
class LinearStateSpaceModel(BaseBlockSSM):
    state_dim: int
    input_dim: int 
    output_dim: int

    def setup(self):
        self._fxx = self.fxx(self.state_dim)
        self._fxu = self.fxu(self.state_dim)
        self._fyx = self.fyx(self.output_dim)
        self._fyu = self.fyu(self.output_dim)

    def __call__(self, state, input):
        return super().__call__(state, input)
    
    class fxx(nn.Module):
        state_dim: int
        def setup(self):
            self.dense = nn.Dense(features=self.state_dim, use_bias=False)

        def __call__(self, x):
            return self.dense(x)

    class fxu(nn.Module):
        state_dim: int
        def setup(self):
            self.dense = nn.Dense(features=self.state_dim, use_bias=False)

        def __call__(self, u):
            return self.dense(u)

    class fyx(nn.Module):
        output_dim: int
        def setup(self):
            self.dense = nn.Dense(features=self.output_dim, use_bias=False)

        def __call__(self, x):
            return self.dense(x)

    class fyu(nn.Module):
        output_dim: int
        def setup(self):
            self.dense = nn.Dense(features=self.output_dim, use_bias=False)

        def __call__(self, x):
            return self.dense(x)
        
lssm = LinearStateSpaceModel(name="lssm", state_dim=3, input_dim=5, output_dim=1)
print(lssm.tabulate(jax.random.PRNGKey(0), jnp.zeros((3,)), jnp.zeros((5,))))
params = lssm.init(jax.random.PRNGKey(0), jnp.zeros((3,)), jnp.zeros((5,)))
print(params)
state = jnp.ones((3,))  # initial state
input = jnp.ones((5,))  # input at the current time step
new_state, output = jax.jit(lssm.apply)(params, state, input)
print(new_state, output)

class RCModel(BaseBlockSSM):
    # need overwrite the learnable parameters using RC parameters
    def setup(self):
        super().setup()
        self.Cai = self.param('Cai', nn.initializers.ones, ())
        self.Cwe = self.param('Cwe', nn.initializers.ones, ())
        self.Cwi = self.param('Cwi', nn.initializers.ones, ())
        self.Re = self.param('Re', nn.initializers.ones, ())
        self.Ri = self.param('Ri', nn.initializers.ones, ())
        self.Rw = self.param('Rw', nn.initializers.ones, ())
        self.Rg = self.param('Rg', nn.initializers.ones, ())

        # overwrite the learnable parameters
        self._fxx = self.fxx(self.Cai, self.Cwe, self.Cwi, self.Re, self.Ri, self.Rw, self.Rg)
        self._fxu = self.fxu(self.Cai, self.Cwe, self.Cwi, self.Re, self.Rg)
        self._fyx = self.fyx()
        self._fyu = self.fyu()

    def __call__(self, state, input):
        return super().__call__(state, input)

    class fxx(nn.Module):
        Cai: float
        Cwe: float
        Cwi: float
        Re: float
        Ri: float
        Rw: float
        Rg: float
        def setup(self):
            A = jnp.zeros((3, 3))
            A = A.at[0, 0].set(-1/self.Cai*(1/self.Rg+1/self.Ri))
            A = A.at[0, 2].set(1/(self.Cai*self.Ri))
            A = A.at[1, 1].set(-1/self.Cwe*(1/self.Re+1/self.Rw))
            A = A.at[1, 2].set(1/(self.Cwe*self.Rw))
            A = A.at[2, 0].set(1/(self.Cwi*self.Ri))
            A = A.at[2, 1].set(1/(self.Cwi*self.Rw))
            A = A.at[2, 2].set(-1/self.Cwi*(1/self.Rw+1/self.Ri))
            self.A = A

        def __call__(self, x):
            return self.A @ x

    class fxu(nn.Module):
        Cai: float
        Cwe: float
        Cwi: float
        Re: float
        Rg: float

        def setup(self):
            B = jnp.zeros((3, 5))
            B = B.at[0, 0].set(1/(self.Cai*self.Rg))
            B = B.at[0, 1].set(1/self.Cai)
            B = B.at[0, 2].set(1/self.Cai)
            B = B.at[1, 0].set(1/(self.Cwe*self.Re))
            B = B.at[1, 3].set(1/self.Cwe)
            B = B.at[2, 4].set(1/self.Cwi)
            self.B = B 

        def __call__(self, u):
            return self.B @ u

    class fyx(nn.Module):
        def setup(self):
            C = jnp.zeros((1, 3))
            C = C.at[0, 0].set(1)
            self.C = C 

        def __call__(self, x):
            return self.C @ x

    class fyu(nn.Module):
        def setup(self):
            self.D = jnp.zeros((1, 5))

        def __call__(self, u):
            return self.D @ u
        

# create model
model = RCModel(name='RC', state_dim=3, input_dim=5, output_dim=1)
print(model.tabulate(jax.random.PRNGKey(0), jnp.zeros((3,)), jnp.zeros((5,))))
params = model.init(jax.random.PRNGKey(0), jnp.zeros((3,)), jnp.zeros((5,)))
print(params)
state = jnp.ones((3,))  # initial state
input = jnp.ones((5,))  # input at the current time step
new_state, output = model.apply(params, state, input)
print(new_state, output)


# forward simulation
@jax.jit
def forward_step(params, state, input):
    new_state, output = model.apply(params, state, input)
    return new_state, output

n_steps = 1000 
n = 0
ts = time.time()
while n < n_steps:
    new_state, output = forward_step(params, state, input)
    state = new_state
    #print(params)
    # apply a fake parameter update 
    params = jax.tree_map(lambda x: x + 0.1, params)
    n += 1
    #print(state, output)
te = time.time()
print(f"forward simulation takes {te-ts} seconds")

