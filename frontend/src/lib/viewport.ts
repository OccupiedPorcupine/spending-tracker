/** Keeps --kb on <html> equal to the height the on-screen keyboard covers, using visualViewport.
 * The sticky save bar sits that far from the bottom, so it stays visible above the keyboard
 * without a position: fixed bar that jumps when the keyboard opens. */
export function trackKeyboard(): void {
  const vv = window.visualViewport;
  if (!vv) return;
  const update = () => {
    const covered = Math.max(0, window.innerHeight - (vv.height + vv.offsetTop));
    document.documentElement.style.setProperty('--kb', `${Math.round(covered)}px`);
    document.documentElement.classList.toggle('kb-open', covered > 80);
  };
  vv.addEventListener('resize', update);
  vv.addEventListener('scroll', update);
  update();
}
