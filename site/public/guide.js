for (const link of document.querySelectorAll('.docs-nav a')) {
  if (new URL(link.href).pathname === location.pathname) link.setAttribute('aria-current', 'page');
}
for (const heading of document.querySelectorAll('.prose h2, .prose h3')) {
  heading.id ||= heading.textContent.toLowerCase().replace(/[^a-z0-9\s-]/g, '').trim().replace(/\s+/g, '-');
}
for (const block of document.querySelectorAll('pre')) {
  const button = document.createElement('button');
  button.className = 'copy-code'; button.type = 'button'; button.textContent = 'Copy';
  button.setAttribute('aria-label', 'Copy command block');
  button.addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(block.querySelector('code').textContent); button.textContent = 'Copied'; }
    catch { button.textContent = 'Select text to copy'; }
  });
  block.prepend(button);
}
