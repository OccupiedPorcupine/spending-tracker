import '@fontsource/barlow-condensed/latin-800.css';
import '@fontsource/jetbrains-mono/latin-600.css';
import '@fontsource/manrope/latin-400.css';
import '@fontsource/manrope/latin-700.css';
import './styles/app.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { installSync } from './lib/queue';
import { registerSW } from './lib/update';
import { trackKeyboard } from './lib/viewport';

trackKeyboard();
installSync();
registerSW();
createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
