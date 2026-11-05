'use strict';

const rows = [...document.querySelectorAll('.row')];
let sel = 0;

function paint() {
  rows.forEach((r, i) => r.classList.toggle('sel', i === sel));
}
function status(t) { document.getElementById('status').textContent = t; }
function setRes(id, t) { document.getElementById('r-' + id).textContent = t; }

// счётчик запусков — доказывает, что localStorage переживает перезагрузку аппа
const boots = (parseInt(localStorage.getItem('boots') || '0', 10) || 0) + 1;
localStorage.setItem('boots', String(boots));
document.getElementById('boot').textContent = '#' + boots;
setRes('storage', 'старт #' + boots);

const actions = {
  async tts() {
    if (!('speechSynthesis' in window)) { setRes('tts', 'НЕТ API'); return; }
    const u = new SpeechSynthesisUtterance('Тест связи. Голос работает.');
    if (!u.voice) {
      const v = speechSynthesis.getVoices().find(v => v.lang.startsWith('ru'));
      if (v) u.voice = v;
    }
    u.onend = () => { setRes('tts', 'OK'); status('TTS: прозвучало до конца'); };
    u.onerror = (e) => { setRes('tts', 'ERR'); status('TTS ошибка: ' + e.error); };
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
    status('TTS: говорю…');
  },

  async net() {
    // api.telegram.org отдаёт Access-Control-Allow-Origin: * (проверено).
    // Ждём HTTP-ответ (401 = сеть есть, токен фиктивный).
    const t0 = performance.now();
    try {
      const r = await fetch('https://api.telegram.org/bot1:smoke/getMe');
      const ms = Math.round(performance.now() - t0);
      setRes('net', 'OK ' + ms + 'ms');
      status('Сеть: HTTP ' + r.status + ' за ' + ms + 'мс — fetch работает');
    } catch (e) {
      setRes('net', 'FAIL');
      status('Сеть: ' + e.message);
    }
  },

  compass() {
    const show = (e) => {
      const hdg = (e.webkitCompassHeading ?? (360 - (e.alpha ?? 0)) % 360);
      setRes('compass', Math.round(hdg) + '°');
      status('Компас: ' + Math.round(hdg) + '° (живой поток)');
    };
    if (typeof DeviceOrientationEvent.requestPermission === 'function') {
      DeviceOrientationEvent.requestPermission().then((s) => {
        if (s === 'granted') { window.addEventListener('deviceorientation', show, true); status('Компас: разрешение дано'); }
        else { setRes('compass', 'DENIED'); status('Компас: разрешение отклонено (' + s + ')'); }
      });
    } else {
      window.addEventListener('deviceorientation', show, true);
      status('Компас: слушаю deviceorientation');
    }
  },

  storage() {
    setRes('storage', 'старт #' + boots);
    status('Память: запуск #' + boots + '. Сделай Restart из меню очков (средний pinch) — номер должен вырасти');
  }
};

function activate() { actions[rows[sel].dataset.act](); }

document.addEventListener('keydown', (e) => {
  switch (e.key) {
    case 'ArrowUp':    sel = (sel + rows.length - 1) % rows.length; paint(); e.preventDefault(); break;
    case 'ArrowDown':  sel = (sel + 1) % rows.length; paint(); e.preventDefault(); break;
    case 'Enter':      activate(); e.preventDefault(); break;
    case 'Escape':     status('Сброшено. ↑↓ · Enter'); document.querySelectorAll('.res').forEach(r => r.textContent = ''); break;
  }
});

paint();
status('Готов. ↑↓ выбор · Enter запуск');
