# Spend icon

`spend-icon.svg` is the master artwork (1024 viewBox): a circuit-trace S with solder pads and a hollow centre node, matching Quant Gym's X.

- Background: `#121A17` to `#070A09`, vertical gradient
- Green: `#8CFFCB` to `#3BEA98` to `#10C473`
- Holes: `#0A0E0C`

Exports live in `frontend/public`. The filenames carry a version suffix (`-v2`) because iOS caches Home Screen and share-sheet icons by URL. If you redraw the icon, export new files with the next suffix, update `index.html` and `manifest.webmanifest`, and keep `/apple-touch-icon.png` in sync, since iOS also requests that path directly.

Re-export with `rsvg-convert -w 180 -h 180 spend-icon.svg -o apple-touch-icon-v3.png` (and the same for 192 and 512).
