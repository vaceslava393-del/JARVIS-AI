'use strict';

// Overrides the legacy voice helpers after app.js has loaded.
let skipNextSpeech = false;

say = function (text) {
  if (!speakEnabled || skipNextSpeech) return;
  const spoken = String(text)
    .replace(/https?:\/\/\S+/g, 'ссылка')
    .replace(/[•#*_`]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

  if (window.AndroidTTS && typeof window.AndroidTTS.speak === 'function') {
    try {
      window.setTimeout(() => window.AndroidTTS.speak(spoken, Number($('rate').value)), 180);
      $('voiceState').textContent = 'Озвучиваю ответ…';
      return;
    } catch (error) {
      $('voiceState').textContent = 'Android TTS: ' + error.message;
    }
  }

  if (!window.speechSynthesis) {
    $('voiceState').textContent = 'Синтез речи недоступен.';
    return;
  }
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(spoken);
  utterance.lang = 'ru-RU';
  utterance.rate = Number($('rate').value);
  const selected = voices.find(voice => voice.name === $('voiceSelect').value);
  if (selected) utterance.voice = selected;
  utterance.onstart = () => $('voiceState').textContent = 'Озвучиваю ответ…';
  utterance.onend = () => $('voiceState').textContent = 'Голосовой ответ включён.';
  utterance.onerror = () => $('voiceState').textContent = 'Ошибка воспроизведения голоса.';
  window.speechSynthesis.speak(utterance);
};

stopVoice = function () {
  skipNextSpeech = true;
  try { window.AndroidTTS?.stop?.(); } catch (_) {}
  window.speechSynthesis?.cancel();
  try { recognition?.abort(); } catch (_) {}
  $('voiceState').textContent = 'Голос и прослушивание остановлены.';
};

$('stopVoice').onclick = stopVoice;
$('testVoice').onclick = () => {
  speakEnabled = true;
  skipNextSpeech = false;
  localStorage.setItem('jarvisSpeak', 'on');
  setSpeakLabel();
  say('Проверка голоса. Джарвис готов к работе.');
};

window.jarvisSpeechResult = text => {
  speakEnabled = true;
  skipNextSpeech = false;
  localStorage.setItem('jarvisSpeak', 'on');
  setSpeakLabel();
  $('voiceState').textContent = 'Распознано: ' + text;
  sendMessage(text);
};

// A new request cancels the previous stop state; pressing Stop while waiting
// still prevents that pending answer from speaking.
const originalSendMessage = sendMessage;
sendMessage = function (text) {
  skipNextSpeech = false;
  return originalSendMessage(text);
};
