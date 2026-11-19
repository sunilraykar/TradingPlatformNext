import React from 'react';
import { createRoot } from 'react-dom/client';
import AuthGate from './auth/AuthGate.jsx';
import './styles.css';
import './typography.css';

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <AuthGate />
  </React.StrictMode>,
);
