// The page's 3D centrepiece: a phone running My Trial Board.
//
// The phone has no layout of its own. Empty `.phone-slot` elements in the page
// mark where it should sit at each stage; every frame the phone is placed over
// the slot for the current scroll stage (or between two slots while moving).
// CSS therefore decides its position and size at every breakpoint.
import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";
import { drawScreen, SCREEN_W, SCREEN_H } from "./screens.js";

const PW = 2.0; // body width
const SCREEN_INSET = 0.07;
const SW = PW - SCREEN_INSET * 2;
const SH = SW * (SCREEN_H / SCREEN_W);
const PH = SH + SCREEN_INSET * 2; // body height
const FRONT_Z = 0.1;
const FOV = 30;
const CAM_Z = 14;
const FLIP_SECONDS = 0.95;
const INTRO_SECONDS = 1.6;

const clamp01 = (v) => Math.min(1, Math.max(0, v));
const lerp = (a, b, t) => a + (b - a) * t;
const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const easeOut = (t) => 1 - Math.pow(1 - t, 4);

function roundedRect(w, h, r) {
  const s = new THREE.Shape();
  const x = -w / 2;
  const y = -h / 2;
  s.moveTo(x + r, y);
  s.lineTo(x + w - r, y);
  s.quadraticCurveTo(x + w, y, x + w, y + r);
  s.lineTo(x + w, y + h - r);
  s.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  s.lineTo(x + r, y + h);
  s.quadraticCurveTo(x, y + h, x, y + h - r);
  s.lineTo(x, y + r);
  s.quadraticCurveTo(x, y, x + r, y);
  return s;
}

// ShapeGeometry UVs are in shape units; remap them to 0..1 across the rect.
function flatRect(w, h, r) {
  const geo = new THREE.ShapeGeometry(roundedRect(w, h, r), 16);
  const pos = geo.attributes.position;
  const uv = geo.attributes.uv;
  for (let i = 0; i < pos.count; i++) uv.setXY(i, pos.getX(i) / w + 0.5, pos.getY(i) / h + 0.5);
  return geo;
}

function canvasTexture(size, draw) {
  const cv = document.createElement("canvas");
  cv.width = size[0];
  cv.height = size[1];
  draw(cv.getContext("2d"), cv.width, cv.height);
  const tex = new THREE.CanvasTexture(cv);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function radialTexture(inner, outer) {
  return canvasTexture([128, 128], (ctx, w) => {
    const g = ctx.createRadialGradient(w / 2, w / 2, 0, w / 2, w / 2, w / 2);
    g.addColorStop(0, inner);
    g.addColorStop(1, outer);
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, w);
  });
}

function buildPhone(screenTexture, maxAnisotropy) {
  const phone = new THREE.Group();

  const bevel = 0.035;
  const depth = FRONT_Z * 2 - bevel * 2;
  const bodyGeo = new THREE.ExtrudeGeometry(roundedRect(PW - bevel * 2, PH - bevel * 2, 0.3 - bevel), {
    depth,
    bevelEnabled: true,
    bevelThickness: bevel,
    bevelSize: bevel,
    bevelSegments: 6,
    curveSegments: 24,
  });
  bodyGeo.translate(0, 0, -depth / 2);
  const frameMat = new THREE.MeshStandardMaterial({ color: 0x2a2f38, metalness: 0.9, roughness: 0.26 });
  phone.add(new THREE.Mesh(bodyGeo, frameMat));

  const glass = new THREE.Mesh(
    flatRect(PW - bevel * 2, PH - bevel * 2, 0.3 - bevel),
    new THREE.MeshStandardMaterial({ color: 0x05070a, metalness: 0.2, roughness: 0.12 }),
  );
  glass.position.z = FRONT_Z + 0.0015;
  phone.add(glass);

  screenTexture.anisotropy = maxAnisotropy;
  const screen = new THREE.Mesh(flatRect(SW, SH, 0.22), new THREE.MeshBasicMaterial({ map: screenTexture, toneMapped: false }));
  screen.position.z = FRONT_Z + 0.003;
  phone.add(screen);

  // A soft diagonal sheen that slides across the glass as the phone turns.
  const glareTex = canvasTexture([256, 256], (ctx, w, h) => {
    const g = ctx.createLinearGradient(0, h, w, 0);
    g.addColorStop(0.3, "rgba(255,255,255,0)");
    g.addColorStop(0.5, "rgba(255,255,255,0.55)");
    g.addColorStop(0.62, "rgba(255,255,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);
  });
  glareTex.wrapS = THREE.RepeatWrapping;
  const glare = new THREE.Mesh(
    flatRect(SW, SH, 0.22),
    new THREE.MeshBasicMaterial({ map: glareTex, transparent: true, opacity: 0.16, depthWrite: false, toneMapped: false }),
  );
  glare.position.z = FRONT_Z + 0.005;
  phone.add(glare);

  // Side buttons
  const buttonGeo = new THREE.BoxGeometry(0.03, 0.46, 0.07);
  [[PW / 2 + 0.005, 0.7, 1], [-PW / 2 - 0.005, 0.95, 0.6], [-PW / 2 - 0.005, 0.38, 0.6]].forEach(([x, y, sy]) => {
    const b = new THREE.Mesh(buttonGeo, frameMat);
    b.position.set(x, y, 0);
    b.scale.y = sy;
    phone.add(b);
  });

  // Back: camera island and the logo mark
  const island = new THREE.Mesh(
    new RoundedBoxGeometry(0.76, 0.76, 0.06, 5, 0.16),
    new THREE.MeshStandardMaterial({ color: 0x343a44, metalness: 0.85, roughness: 0.3 }),
  );
  island.position.set(0.44, PH / 2 - 0.62, -FRONT_Z - 0.02);
  phone.add(island);
  const lensGeo = new THREE.CylinderGeometry(0.13, 0.13, 0.04, 32);
  lensGeo.rotateX(Math.PI / 2);
  const lensMat = new THREE.MeshStandardMaterial({ color: 0x0a0d12, metalness: 0.6, roughness: 0.08 });
  [[0.27, 0.17], [0.61, -0.17]].forEach(([x, dy]) => {
    const lens = new THREE.Mesh(lensGeo, lensMat);
    lens.position.set(x, PH / 2 - 0.62 + dy, -FRONT_Z - 0.06);
    phone.add(lens);
  });
  const logoTex = canvasTexture([256, 256], (ctx) => {
    ctx.scale(4, 4);
    ctx.strokeStyle = "rgba(255,255,255,0.5)";
    ctx.lineWidth = 5;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(15, 45.5);
    ctx.lineTo(22.5, 21.5);
    ctx.lineTo(32, 39);
    ctx.lineTo(41.5, 21.5);
    ctx.lineTo(49, 45.5);
    ctx.stroke();
    ctx.fillStyle = "#FF8A4C";
    ctx.beginPath();
    ctx.arc(32, 20, 3.6, 0, Math.PI * 2);
    ctx.fill();
  });
  const logo = new THREE.Mesh(new THREE.PlaneGeometry(0.62, 0.62), new THREE.MeshBasicMaterial({ map: logoTex, transparent: true, toneMapped: false }));
  logo.position.set(0, -0.1, -FRONT_Z - 0.002);
  logo.rotation.y = Math.PI;
  phone.add(logo);

  return { phone, glareTex };
}

function buildDecor() {
  // Soft shapes that live behind the phone. Everything here stays further back
  // than the phone's edge reaches while it turns, so nothing crosses the screen.
  const decor = new THREE.Group();
  const glossy = (color) => new THREE.MeshStandardMaterial({ color, metalness: 0.15, roughness: 0.22 });

  // One thin halo with a bead travelling round it: the next visit coming up.
  const ring = new THREE.Mesh(new THREE.TorusGeometry(2.55, 0.016, 12, 160), glossy(0x9dbdf7));
  ring.rotation.set(0.5, 0.26, 0);
  ring.position.z = -2.4;
  const bead = new THREE.Mesh(new THREE.SphereGeometry(0.075, 24, 16), new THREE.MeshStandardMaterial({ color: 0xff8a4c, metalness: 0.1, roughness: 0.3 }));
  bead.position.x = 2.55;
  ring.add(bead);
  decor.add(ring);

  const orbGeo = new THREE.SphereGeometry(1, 40, 28);
  const orbs = [
    [-2.2, 1.7, -1.5, 0.2, 0xbcd3ff],
    [2.3, -1.45, -1.4, 0.25, 0xa9e6dc],
    [1.95, 2.15, -2.0, 0.13, 0xffc9a8],
    [-2.3, -1.6, -1.8, 0.12, 0xffc9a8],
  ].map(([x, y, z, r, color], i) => {
    const m = new THREE.Mesh(orbGeo, glossy(color));
    m.position.set(x, y, z);
    m.scale.setScalar(r);
    m.userData = { x, y, phase: i * 1.7, speed: 0.5 + (i % 3) * 0.17 };
    decor.add(m);
    return m;
  });
  return { decor, ring, orbs };
}

export function createPhoneScene(canvas, { slots, initialScreen, reducedMotion = false }) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setClearColor(0x000000, 0);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  pmrem.dispose();

  const camera = new THREE.PerspectiveCamera(FOV, 1, 0.1, 60);
  camera.position.set(0, 0, CAM_Z);
  const key = new THREE.DirectionalLight(0xffffff, 1.2);
  key.position.set(3, 5, 8);
  scene.add(key);

  // Screen: one canvas, redrawn while the phone's back faces the camera.
  const screenCanvas = document.createElement("canvas");
  let screenScale = 2;
  let shownScreen = initialScreen;
  let wantedScreen = initialScreen;
  drawScreen(screenCanvas, shownScreen, screenScale);
  const screenTexture = new THREE.CanvasTexture(screenCanvas);
  screenTexture.colorSpace = THREE.SRGBColorSpace;
  screenTexture.generateMipmaps = true;
  screenTexture.minFilter = THREE.LinearMipmapLinearFilter;

  const rig = new THREE.Group(); // position + scale from the slot
  const { phone, glareTex } = buildPhone(screenTexture, renderer.capabilities.getMaxAnisotropy());
  rig.add(phone);
  scene.add(rig);

  const { decor, ring, orbs } = buildDecor();
  scene.add(decor);

  const aura = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: radialTexture("rgba(64,132,255,0.32)", "rgba(64,132,255,0)"), transparent: true, depthWrite: false, toneMapped: false }),
  );
  aura.position.z = -3.5;
  scene.add(aura);
  const shadow = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: radialTexture("rgba(13,42,79,0.34)", "rgba(13,42,79,0)"), transparent: true, depthWrite: false, toneMapped: false }),
  );
  scene.add(shadow);

  // ── State ──
  const LAST = slots.length - 1;
  let target = 0;
  let S = 0;
  let active = true;
  let raf = 0;
  let last = performance.now();
  let time = 0;
  let intro = reducedMotion ? 1 : 0;
  let flip = 1; // 0..1 while spinning, 1 at rest
  let flipDir = 1;
  let swapped = true;
  let lastX = 0;
  const pointer = { x: 0, y: 0, sx: 0, sy: 0 };
  const view = { w: 1, h: 1 };
  const worldH = 2 * Math.tan(THREE.MathUtils.degToRad(FOV / 2)) * CAM_Z;
  const decorPos = new THREE.Vector3();
  let decorPlaced = false;

  function slotPose(i) {
    const r = slots[i].getBoundingClientRect();
    const worldW = worldH * (view.w / view.h);
    const cx = r.left + r.width / 2;
    const cy = r.top + r.height / 2;
    return {
      x: (cx / view.w - 0.5) * worldW,
      y: (0.5 - cy / view.h) * worldH,
      scale: ((r.height / view.h) * worldH * 0.94) / PH,
      side: Math.max(-1, Math.min(1, (cx / view.w - 0.5) * 4)), // -1 left of page, +1 right
    };
  }

  function showScreen(id) {
    shownScreen = id;
    drawScreen(screenCanvas, id, screenScale);
    screenTexture.needsUpdate = true;
  }

  function update(dt) {
    if (!reducedMotion) time += dt;
    S = reducedMotion ? target : S + (target - S) * (1 - Math.exp(-dt * 5));
    if (Math.abs(target - S) < 0.0005) S = target;
    intro = Math.min(1, intro + dt / INTRO_SECONDS);
    const introLeft = 1 - easeOut(intro);

    // Where: over the current slot, or between two slots while travelling.
    const a = Math.min(Math.floor(S), Math.max(0, LAST - 1));
    const t = LAST === 0 ? 0 : clamp01(S - a);
    const A = slotPose(a);
    const B = LAST === 0 ? A : slotPose(a + 1);
    const te = easeInOut(t);
    const x = lerp(A.x, B.x, te);
    const y = lerp(A.y, B.y, te);
    const scale = Math.max(0.0001, lerp(A.scale, B.scale, te));
    const side = lerp(A.side, B.side, te);

    // Screen change: one full turn, the new screen is drawn while the back shows.
    if (flip >= 1 && wantedScreen !== shownScreen) {
      if (reducedMotion) showScreen(wantedScreen);
      else {
        flip = 0;
        swapped = false;
        flipDir = x >= lastX ? 1 : -1;
      }
    }
    if (flip < 1) {
      flip = Math.min(1, flip + dt / FLIP_SECONDS);
      if (!swapped && flip >= 0.5) {
        showScreen(wantedScreen);
        swapped = true;
      }
    }
    lastX = x;
    const spin = flip < 1 ? flipDir * Math.PI * 2 * easeInOut(flip) : 0;
    const dip = flip < 1 ? 1 - 0.07 * Math.sin(Math.PI * flip) : 1;

    pointer.sx += (pointer.x - pointer.sx) * Math.min(1, dt * 3.5);
    pointer.sy += (pointer.y - pointer.sy) * Math.min(1, dt * 3.5);

    const bob = Math.sin(time * 0.9) * 0.06;
    rig.position.set(x, y + bob * scale - introLeft * 2.4, 0);
    rig.scale.setScalar(scale * dip * (1 - introLeft * 0.15));
    // Rest pose turns the screen slightly towards the copy beside it.
    const restY = -(0.12 + 0.24 * Math.abs(side)) * (side >= 0 ? 1 : -1);
    phone.rotation.set(
      0.06 + pointer.sy * 0.1 + Math.sin(time * 0.6) * 0.02,
      restY + spin + pointer.sx * 0.16 - introLeft * 2.6,
      -restY * 0.14 + Math.sin(time * 0.45) * 0.01,
    );
    glareTex.offset.x = phone.rotation.y * 0.45 + 0.1;

    // Backdrop follows with a little lag, which reads as depth.
    if (!decorPlaced) {
      decorPos.set(x, y, 0);
      decorPlaced = true;
    }
    decorPos.x += (x - decorPos.x) * Math.min(1, dt * 3);
    decorPos.y += (y - decorPos.y) * Math.min(1, dt * 3);
    decor.position.copy(decorPos);
    decor.scale.setScalar(scale * (1 - introLeft * 0.4));
    decor.rotation.set(pointer.sy * 0.05, pointer.sx * 0.08, 0);
    ring.rotation.z = time * 0.22;
    for (const o of orbs) {
      o.position.x = o.userData.x + Math.cos(time * o.userData.speed * 0.6 + o.userData.phase) * 0.14;
      o.position.y = o.userData.y + Math.sin(time * o.userData.speed + o.userData.phase) * 0.2;
    }
    aura.position.set(decorPos.x, decorPos.y, -3.5);
    aura.scale.setScalar(9.5 * scale);
    const footprint = 0.45 + 0.55 * Math.abs(Math.cos(phone.rotation.y));
    shadow.position.set(x, y - (PH / 2 + 0.42) * scale - introLeft * 2.4, -0.4);
    shadow.scale.set(3.3 * scale * footprint, 0.5 * scale, 1);
    shadow.material.opacity = 0.9 * (1 - introLeft);
  }

  function frame(now) {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    update(dt);
    renderer.render(scene, camera);
  }
  function start() {
    if (raf || !active || document.hidden) return;
    last = performance.now();
    raf = requestAnimationFrame(frame);
  }
  function stop() {
    cancelAnimationFrame(raf);
    raf = 0;
  }

  function resize() {
    // Fall back to the window if the canvas has not been laid out yet.
    const w = canvas.clientWidth || window.innerWidth;
    const h = canvas.clientHeight || window.innerHeight;
    if (!w || !h) return;
    view.w = w;
    view.h = h;
    const small = w < 700;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, small ? 1.75 : 2));
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    const wantScale = small ? 1.5 : 2.5;
    if (wantScale !== screenScale) {
      screenScale = wantScale;
      showScreen(shownScreen);
    }
  }
  const ro = new ResizeObserver(resize);
  ro.observe(canvas);
  resize();

  const onPointer = (e) => {
    if (reducedMotion || e.pointerType === "touch") return;
    pointer.x = (e.clientX / window.innerWidth) * 2 - 1;
    pointer.y = (e.clientY / window.innerHeight) * 2 - 1;
  };
  const onVisibility = () => (document.hidden ? stop() : start());
  window.addEventListener("pointermove", onPointer, { passive: true });
  document.addEventListener("visibilitychange", onVisibility);

  update(0);
  renderer.render(scene, camera);
  start();

  return {
    /** Scroll position as a slot index; fractions are positions between slots. */
    setStage(value, { instant = false } = {}) {
      target = Math.min(LAST, Math.max(0, value));
      if (instant) S = target;
    },
    /** Ask for a screen. The phone turns once and shows it. */
    setScreen(id, { instant = false } = {}) {
      wantedScreen = id;
      if (instant && flip >= 1) showScreen(id);
    },
    setActive(on) {
      active = on;
      if (on) start();
      else stop();
    },
    dispose() {
      stop();
      ro.disconnect();
      window.removeEventListener("pointermove", onPointer);
      document.removeEventListener("visibilitychange", onVisibility);
      renderer.dispose();
    },
  };
}
