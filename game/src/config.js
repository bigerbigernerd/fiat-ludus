// Every gameplay tuning number lives here. Units: metres, seconds, radians.
export const CFG = {
  simDt: 1 / 60,
  maxFrameDt: 0.1,
  waterLevel: 0,

  player: {
    radius: 0.35,
    height: 1.5,
    walkSpeed: 2.6,
    runSpeed: 6.0,
    accel: 34,
    decel: 26,
    airControl: 0.5,
    turnRate: 13,
    gravity: -27,
    jumpVelocity: 9.2,
    jumpCut: 0.5, // vertical speed multiplier when jump is released early
    coyoteTime: 0.12,
    jumpBuffer: 0.15,
    slopeMax: 0.9, // rise over run; steeper is rock you cannot climb
    maxHealth: 5,
    invulnTime: 1.3,
    hurtKnockback: 7,
    seedsPerPetal: 10,
    stepDistance: 1.55,
    jumpClipTime: 0.9, // a longer jump clip is sped up to about this length
    attack: {
      duration: 0.34,
      activeFrom: 0.05,
      activeTo: 0.2,
      range: 1.75,
      arc: 2.0, // total radians of the swing cone
      lunge: 6.0,
      cooldown: 0.1,
      damage: 1,
      autoAimRange: 3.2,
      autoAimArc: 1.4,
      clipTime: 0.6, // a longer attack clip is sped up to about this length (visual only)
    },
  },

  enemies: {
    mossling: {
      hp: 3,
      radius: 0.48,
      aggroRange: 8.5,
      leash: 14,
      wanderRadius: 2.5,
      hopTime: 0.42,
      hopPause: [0.25, 0.8],
      hopHeight: 0.35,
      chaseSpeed: 3.1,
      wanderSpeed: 1.4,
      attackRange: 2.7,
      telegraph: 0.6,
      lungeSpeed: 9.5,
      lungeTime: 0.3,
      recover: 0.95,
      hurtStun: 0.38,
      knockback: 7.5,
      damage: 1,
      seedDrop: 3,
    },
  },

  camera: {
    fov: 55,
    distance: 6.4,
    minDistance: 1.4,
    maxDistance: 10,
    pitch: 0.34,
    pitchMin: -0.2,
    pitchMax: 1.15,
    targetHeight: 1.2,
    followRate: 12,
    autoYawDelay: 1.2,
    autoYawRate: 1.5,
    dragSensitivity: 0.0055,
    touchSensitivity: 0.0085,
    clearance: 0.45,
  },

  lantern: { interactRange: 2.5, lightTime: 1.1, glowRadius: 15 },
  gate: { openTime: 3.2, winDepth: 1.6 },

  feel: {
    hitStop: 0.07,
    hitStopHurt: 0.1,
    traumaHit: 0.32,
    traumaHurt: 0.55,
    traumaLantern: 0.4,
    traumaDecay: 1.7,
  },

  render: {
    pixelRatioMax: 2,
    pixelRatioMaxMobile: 1.6,
    shadowMapSize: 2048,
    shadowMapSizeMobile: 1024,
    shadowExtent: 26,
    grassClumps: 10500, // three blades (9 triangles) per clump
    grassClumpsMobile: 4800,
    grassTile: 40,
    outlinePx: 2.0,
    fogNear: 40,
    fogFar: 190,
    // Per kind of prop, outline included. A kind above this draws fewer scattered copies
    // (hand-placed ones always stay). Per-copy budgets are in the manifest ("triangles").
    maxTrianglesPerPropSet: 110000,
    maxTrianglesPerPropSetMobile: 50000,
  },
};
