/** Amount keypad in calculator order, so the iOS keyboard never opens for the amount.
 *
 *   7 8 9
 *   4 5 6
 *   1 2 3
 *   . 0 ⌫
 */
export function Keypad({ onKey }: { onKey: (k: string) => void }) {
  const key = (k: string, label = k, aria?: string, cls?: string) => (
    <button type="button" key={k} className={cls} aria-label={aria ?? label} onClick={() => onKey(k)}>{label}</button>
  );
  return (
    <div
      className="pad"
      // keep taps from moving focus (and from closing the keyboard if Notes is focused)
      onPointerDown={(e) => { if ((e.target as HTMLElement).closest('button')) e.preventDefault(); }}
      onMouseDown={(e) => { if ((e.target as HTMLElement).closest('button')) e.preventDefault(); }}
    >
      {['7', '8', '9', '4', '5', '6', '1', '2', '3'].map((d) => key(d))}
      {key('.', '.', 'decimal point', 'sym')}
      {key('0')}
      {key('back', '⌫', 'delete last digit', 'fn')}
    </div>
  );
}
