import "@fontsource-variable/plus-jakarta-sans";
import "./styles.css";
import { drawScreen, screenFontsReady } from "./screens.js";

// Where "Request a demo" submissions are addressed. Replace before going live.
const SITE = { contactEmail: "demo@mytrialboard.example" };

const root = document.documentElement;
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// The phone's stops, in page order: hero, the five steps, then the roles section.
const slots = $$(".phone-slot");
const steps = $$(".step");
const rolesSlot = $("#roles-slot");
const ROLES_STAGE = slots.indexOf(rolesSlot);
const tabs = $$('.role-tabs [role="tab"]');
const screenForStage = (i) => slots[i].dataset.screen;

/* ── 3D phone: loaded after first paint so the headline is never blocked ── */
let phone3d = null;

function webglAvailable() {
  try {
    const c = document.createElement("canvas");
    return !!(window.WebGLRenderingContext && (c.getContext("webgl2") || c.getContext("webgl")));
  } catch {
    return false;
  }
}

// Without WebGL every slot gets the same screen on a flat CSS phone frame.
function staticPhones() {
  root.classList.add("no-webgl");
  for (const slot of slots) {
    const frame = document.createElement("div");
    frame.className = "css-phone";
    frame.append(drawScreen(document.createElement("canvas"), slot.dataset.screen, 2));
    slot.replaceChildren(frame);
  }
}

async function bootPhone() {
  await screenFontsReady();
  if (!webglAvailable()) return staticPhones();
  try {
    const { createPhoneScene } = await import("./phone.js");
    phone3d = createPhoneScene($("#scene"), { slots, initialScreen: screenForStage(0), reducedMotion });
    root.classList.add("webgl");
    currentStage = -1; // force the screen for wherever the page is scrolled to
    syncStage(true);
    // Render only while a section that holds a slot is on screen.
    const showing = new Set();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) e.isIntersecting ? showing.add(e.target) : showing.delete(e.target);
        phone3d.setActive(showing.size > 0);
      },
      { rootMargin: "15% 0px" },
    );
    [$("#top"), $("#how"), $("#roles")].forEach((el) => io.observe(el));
  } catch (err) {
    console.error("3D phone failed to start", err);
    staticPhones();
  }
}

/* ── Scroll position → stage (the index of the slot the phone should be on) ── */
const plateau = (f) => {
  // Stay with a slot while its copy is being read; travel between slots quickly.
  const t = Math.min(1, Math.max(0, (f - 0.3) / 0.4));
  return t * t * (3 - 2 * t);
};

function stageFromScroll() {
  const mid = window.innerHeight / 2;
  const centers = slots.map((el) => {
    const r = el.getBoundingClientRect();
    return r.top + r.height / 2;
  });
  if (mid <= centers[0]) return 0;
  for (let i = 0; i < centers.length - 1; i++) {
    if (mid < centers[i + 1]) return i + plateau((mid - centers[i]) / (centers[i + 1] - centers[i]));
  }
  return centers.length - 1;
}

let currentStage = -1;
function syncStage(instant = false) {
  const stage = stageFromScroll();
  phone3d?.setStage(stage, { instant });
  const nearest = Math.round(stage);
  if (nearest !== currentStage) {
    currentStage = nearest;
    phone3d?.setScreen(screenForStage(nearest), { instant });
    steps.forEach((el, i) => el.classList.toggle("is-active", nearest === i + 1));
  }
  const max = document.documentElement.scrollHeight - window.innerHeight;
  root.style.setProperty("--p", max > 0 ? (window.scrollY / max).toFixed(4) : "0");
  $("#nav").classList.toggle("scrolled", window.scrollY > 24);
}

let ticking = false;
function onScroll() {
  if (ticking) return;
  ticking = true;
  requestAnimationFrame(() => {
    ticking = false;
    syncStage();
  });
}
window.addEventListener("scroll", onScroll, { passive: true });
window.addEventListener("resize", onScroll);

/* ── Reveal on scroll, with number count-up ── */
function countUp(el) {
  const end = Number(el.dataset.count);
  if (reducedMotion || !Number.isFinite(end)) return;
  const t0 = performance.now();
  const tick = (now) => {
    const t = Math.min(1, (now - t0) / 1200);
    el.textContent = Math.round(end * (1 - Math.pow(1 - t, 3))).toLocaleString("en-US");
    if (t < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}
const revealIO = new IntersectionObserver(
  (entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      e.target.classList.add("in");
      $$("[data-count]", e.target).forEach(countUp);
      revealIO.unobserve(e.target);
    }
  },
  { threshold: 0.18, rootMargin: "0px 0px -6% 0px" },
);
$$(".reveal").forEach((el) => revealIO.observe(el));

/* ── Hero cards drift a little with the pointer ── */
const heroVisual = $(".hero-visual");
if (!reducedMotion && window.matchMedia("(hover: hover)").matches) {
  window.addEventListener(
    "pointermove",
    (e) => {
      heroVisual.style.setProperty("--px", ((e.clientX / window.innerWidth) * 2 - 1).toFixed(3));
      heroVisual.style.setProperty("--py", ((e.clientY / window.innerHeight) * 2 - 1).toFixed(3));
    },
    { passive: true },
  );
}

/* ── Mobile menu ── */
const navToggle = $(".nav-toggle");
const navLinks = $("#nav-links");
function setMenu(open) {
  navLinks.classList.toggle("open", open);
  navToggle.setAttribute("aria-expanded", String(open));
  navToggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
}
navToggle.addEventListener("click", () => setMenu(navToggle.getAttribute("aria-expanded") !== "true"));
navLinks.addEventListener("click", (e) => e.target.closest("a") && setMenu(false));
document.addEventListener("keydown", (e) => e.key === "Escape" && setMenu(false));

/* ── Role tabs: each role changes the screen on the phone ── */
function selectTab(tab, focus = false) {
  for (const t of tabs) {
    const on = t === tab;
    t.setAttribute("aria-selected", String(on));
    t.tabIndex = on ? 0 : -1;
    $("#" + t.getAttribute("aria-controls")).hidden = !on;
  }
  rolesSlot.dataset.screen = tab.dataset.screen;
  if (phone3d) {
    if (currentStage === ROLES_STAGE) phone3d.setScreen(tab.dataset.screen);
  } else if (root.classList.contains("no-webgl")) {
    drawScreen($("canvas", rolesSlot), tab.dataset.screen, 2);
  }
  if (focus) tab.focus();
  tab.scrollIntoView({ block: "nearest", inline: "nearest", behavior: reducedMotion ? "auto" : "smooth" });
}
tabs.forEach((tab, i) => {
  tab.addEventListener("click", () => selectTab(tab));
  tab.addEventListener("keydown", (e) => {
    const move = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
    if (move) {
      e.preventDefault();
      selectTab(tabs[(i + move + tabs.length) % tabs.length], true);
    } else if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      selectTab(tabs[e.key === "Home" ? 0 : tabs.length - 1], true);
    }
  });
});

/* ── Demo request form ── */
const form = $("#demo-form");
const statusEl = $(".form-status", form);
function validate(field) {
  const input = $("input, select", field);
  if (!input || !input.required) return true;
  const ok = input.value.trim() !== "" && input.checkValidity();
  field.classList.toggle("invalid", !ok);
  $(".err", field).hidden = ok;
  input.setAttribute("aria-invalid", String(!ok));
  return ok;
}
$$(".field", form).forEach((field) => {
  const input = $("input, select", field);
  input?.addEventListener("blur", () => field.classList.contains("invalid") && validate(field));
});
form.addEventListener("submit", (e) => {
  e.preventDefault();
  const invalid = $$(".field", form).filter((f) => !validate(f));
  if (invalid.length) {
    $("input, select", invalid[0]).focus();
    statusEl.textContent = "";
    return;
  }
  const d = Object.fromEntries(new FormData(form));
  const body = [`Name: ${d.name}`, `Email: ${d.email}`, `Organization: ${d.org}`, `Organization type: ${d.type}`, "", d.message || ""].join("\n");
  const subject = `Demo request: ${d.org}`;
  window.location.href = `mailto:${SITE.contactEmail}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
  statusEl.textContent = "Your email app should open with the request ready to send.";
});

$("#year").textContent = new Date().getFullYear();

syncStage(true);
if (document.readyState === "complete") bootPhone();
else window.addEventListener("load", bootPhone, { once: true });
