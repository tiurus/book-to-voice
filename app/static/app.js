const $ = (selector) => document.querySelector(selector);
const text = $('#text');
const counter = $('#counter');
const form = $('#speech-form');
const submit = $('#submit');
const message = $('#message');
const result = $('#result');
const player = $('#player');
const health = $('#health');
let currentFileId = null;

const formatBytes = (bytes) => bytes < 1024 * 1024
  ? `${Math.round(bytes / 1024)} КБ`
  : `${(bytes / 1024 / 1024).toFixed(1)} МБ`;

function showError(value) {
  message.textContent = value;
  message.hidden = false;
}

function clearError() {
  message.hidden = true;
  message.textContent = '';
}

function setLoading(loading, label = 'Озвучить текст') {
  submit.disabled = loading;
  submit.classList.toggle('loading', loading);
  submit.querySelector('.button-label').textContent = label;
}

async function getJson(url, options) {
  const response = await fetch(url, options);
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data?.detail;
    throw new Error(typeof detail === 'string' ? detail : detail?.message || 'Сервис временно недоступен');
  }
  return data;
}

async function checkHealth() {
  try {
    const data = await getJson('/api/health');
    health.className = `health ${data.model_ready ? 'ready' : 'error'}`;
    health.innerHTML = `<span></span>${data.model_ready ? `Модель ${data.model} готова` : 'Модель недоступна'}`;
    submit.disabled = !data.model_ready;
  } catch {
    health.className = 'health error';
    health.innerHTML = '<span></span>Сервис недоступен';
    submit.disabled = true;
  }
}

async function waitForJob(jobId) {
  for (;;) {
    const job = await getJson(`/api/jobs/${jobId}`);
    if (job.state === 'completed') return job;
    if (job.state === 'failed') throw new Error(job.error || 'Не удалось создать запись');
    const label = job.state === 'queued' && job.position > 1
      ? `В очереди: ${job.position}`
      : 'Создаём запись…';
    setLoading(true, label);
    await new Promise((resolve) => setTimeout(resolve, 900));
  }
}

function showResult(job) {
  currentFileId = job.file_id;
  player.src = job.audio.mp3.url;
  $('#download-wav').href = job.audio.wav.download_url;
  $('#download-mp3').href = job.audio.mp3.download_url;
  $('#metadata').textContent = `${job.audio.wav.duration_seconds.toFixed(1)} сек · WAV ${formatBytes(job.audio.wav.size_bytes)} · MP3 ${formatBytes(job.audio.mp3.size_bytes)}`;
  result.hidden = false;
  result.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

text.addEventListener('input', () => {
  counter.textContent = `${text.value.length.toLocaleString('ru-RU')} / 5 000`;
  counter.style.color = text.value.length >= 4900 ? 'var(--red)' : '';
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  clearError();
  if (!text.value.trim()) {
    showError('Введите текст для озвучки.');
    text.focus();
    return;
  }
  setLoading(true, 'Ставим в очередь…');
  try {
    const payload = {
      text: text.value,
      voice: $('#voice').value,
      sample_rate: Number($('#sample-rate').value),
      speed: form.elements.speed.value,
      auto_stress: $('#auto-stress').checked,
      ssml: $('#ssml').checked,
    };
    const queued = await getJson('/api/speech', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const completed = await waitForJob(queued.job_id);
    showResult(completed);
  } catch (error) {
    showError(error.message);
  } finally {
    setLoading(false);
  }
});

$('#delete').addEventListener('click', async () => {
  if (!currentFileId) return;
  try {
    await getJson(`/api/audio/${currentFileId}`, { method: 'DELETE' });
    player.pause();
    player.removeAttribute('src');
    result.hidden = true;
    currentFileId = null;
  } catch (error) {
    showError(error.message);
  }
});

checkHealth();

