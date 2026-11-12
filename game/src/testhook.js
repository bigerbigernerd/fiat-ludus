// window.__game: the contract automated agents use to drive and verify a build.
// With ?test=1 the simulation only advances through step(), on a fixed timestep
// with seeded randomness, so the same inputs give the same results.
export function installTestHook(api) {
  const hook = {
    test: api.TEST,
    ready: api.ready.then(() => true),
    config: api.CFG,
    state: () => api.state(),
    // step(dt, n, draw=true): advance n fixed steps, then render once (skip with draw=false).
    step(dt = api.CFG.simDt, n = 1, draw = true) {
      if (!api.TEST) console.warn('__game.step() outside ?test=1: real-time loop is also running');
      return api.step(dt, n, draw);
    },
    input: (name, value) => api.input(name, value),
    teleport: (x, z) => api.teleport(x, z),
    start: () => api.start(),
    probe: (x, z) => api.probe(x, z),
    reloadAssets: () => api.reloadAssets(),
    render: () => api.render(),
    breakdown: () => api.breakdown(),
    placements: (id) => api.placements(id),
    heroDebug: (boneName) => api.heroDebug(boneName),
    heroVisibility: () => api.heroVisibility(),
  };
  window.__game = hook;
  return hook;
}
