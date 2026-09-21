document.querySelector('.code-window pre').tabIndex = 0;
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
let motion;
const motionButton = document.querySelector('.motion-toggle');
function animate() {
  motion?.revert();
  if (reduced.matches || document.body.classList.contains('paused') || !window.gsap) return;
  gsap.registerPlugin(ScrollTrigger);
  motion = gsap.context(() => {
    gsap.fromTo('.console-preview', { scale: .8 }, { scale: 1, ease: 'none', scrollTrigger: { trigger: '.console-preview', start: 'top 95%', end: 'top 35%', scrub: 1 } });
    gsap.to('.console-preview', { opacity: .2, ease: 'none', scrollTrigger: { trigger: '.console-preview', start: 'bottom 35%', end: 'bottom top', scrub: 1 } });
    gsap.fromTo('.reveal-word', { opacity: .1 }, { opacity: 1, stagger: .15, ease: 'none', scrollTrigger: { trigger: '.reveal-text', start: 'top 85%', end: 'bottom 40%', scrub: 1 } });
  });
}
const reveal = document.querySelector('.reveal-text');
const sentence = reveal.textContent;
reveal.replaceChildren(...sentence.split(' ').map(word => { const span = document.createElement('span'); span.className = 'reveal-word'; span.textContent = word + ' '; return span; }));
motionButton.addEventListener('click', () => {
  const paused = document.body.classList.toggle('paused');
  motionButton.setAttribute('aria-pressed', String(paused));
  motionButton.textContent = paused ? 'Resume motion' : 'Pause motion';
  animate();
});
reduced.addEventListener('change', animate);
animate();
for (const button of document.querySelectorAll('.step > button')) {
  button.addEventListener('click', () => {
    for (const other of document.querySelectorAll('.step > button')) {
      const selected = other === button;
      other.setAttribute('aria-expanded', String(selected));
      other.parentElement.classList.toggle('expanded', selected);
      document.getElementById(other.getAttribute('aria-controls')).hidden = !selected;
    }
  });
}
const examples = [
  { file: 'client.ts', caption: 'Use your approved HTTPS endpoint and client-safe anon key.', code: "import { createClient } from '@supabase/supabase-js'\n\nconst supabase = createClient(\n  'https://api.example.test',\n  'YOUR_CLIENT_SAFE_ANON_KEY',\n  { db: { schema: 'api' } }\n)" },
  { file: 'projects.ts', caption: 'Create your table, grants, and ownership policies before querying.', code: "const { data, error } = await supabase\n  .from('projects')\n  .select('id, name')\n\nif (error) throw error\n\n// RLS decides which rows this user can access.\nconsole.log(data)" },
  { file: 'sign-in.ts', caption: 'Use Auth sessions for users. Never trust a browser app label as identity.', code: "const { data, error } = await supabase.auth\n  .signInWithPassword({\n    email: form.email,\n    password: form.password\n  })\n\nif (error) throw error\n// The client sends the user token on later requests." }
];
for (const tab of document.querySelectorAll('.code-tab')) {
  tab.addEventListener('click', () => {
    for (const other of document.querySelectorAll('.code-tab')) {
      other.classList.toggle('selected', other === tab);
      other.setAttribute('aria-pressed', String(other === tab));
    }
    const example = examples[Number(tab.dataset.example)];
    document.getElementById('code-example').textContent = example.code;
    document.getElementById('code-filename').textContent = example.file;
    document.getElementById('code-caption').textContent = example.caption;
  });
}
document.getElementById('copy-example').addEventListener('click', async event => {
  const button = event.currentTarget;
  try { await navigator.clipboard.writeText(document.getElementById('code-example').textContent); button.textContent = 'Copied'; }
  catch { document.getElementById('copy-example').textContent = 'Select code to copy'; }
});
