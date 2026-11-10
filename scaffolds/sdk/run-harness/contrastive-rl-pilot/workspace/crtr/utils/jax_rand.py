import jax
import jax.random as jr 

def set_seed(seed):
    global _rng
    _rng = jr.PRNGKey(seed)

def next_key():
    global _rng
    _rng, subkey = jr.split(_rng)
    return subkey