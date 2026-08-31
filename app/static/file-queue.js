const $ = (selector) => document.querySelector(selector);
const form = $('#file-form');
const fileInput = $('#text-file');
const submit = $('#file-submit');
const message = $('#file-message');
const queueList = $('#queue-list');
const queueCount = $('#queue-count');
const health = $('#health');
let pollTimer = null;

const stageNames = {
  queued: 'В очереди',
  synthesizing: 'Озвучиваем',
  merging: 'Собираем WAV',
  converting: 'Создаём MP3',
  completed: 'Готово',
  failed: 'Ошибка',
};

const formatBytes = (bytes) => bytes < 1024 * 1024
  ? `${Math.round(bytes / 1024)} КБ`
  : `${(bytes / 1024 / 1024).toFixed(1)} МБ`;

const formatDuration = (seconds) => {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const rest = Math.round(seconds % 60);
  return [hours, minutes, rest].map((part, index) => index ? String(part).padStart(2, '0') : String(part)).join(':');
};

const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (character) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
})[character]);

function showError(value) {
  message.textContent = value;
  message.hidden = false;
}

function setLoading(loading) {
  submit.disabled = loading;
  submit.classList.toggle('loading', loading);
  submit.querySelector('.button-label').textContent = loading ? 'Загружаем…' : 'Добавить в очередь';
}

async function getJson(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data?.detail;
    throw new Error(typeof detail === 'string' ? detail : 'Сервис временно недоступен');
  }
  return data;
}

function cardTemplate(job) {
  const active = ['queued', 'processing'].includes(job.state);
  const status = job.state === 'processing' ? job.stage : job.state;
  const progress = job.state === 'completed' ? 100 : job.progress;
  const fragments = job.total_fragments
    ? `${job.processed_fragments} из ${job.total_fragments} фрагментов`
    : job.state === 'queued' ? `Позиция в очереди: ${job.position || 1}` : 'Подготавливаем текст';
  let result = '';
  if (job.state === 'completed') {
    result = `
      <audio controls preload="metadata" src="${job.audio.mp3.url}"></audio>
      <div class="file-result-meta">${formatDuration(job.audio.wav.duration_seconds)} · WAV ${formatBytes(job.audio.wav.size_bytes)} · MP3 ${formatBytes(job.audio.mp3.size_bytes)}</div>
      <div class="actions">
        <a class="secondary" href="${job.audio.wav.download_url}" download>Скачать WAV</a>
        <a class="secondary" href="${job.audio.mp3.download_url}" download>Скачать MP3</a>
      </div>`;
  } else if (job.state === 'failed') {
    result = `<p class="file-error">${escapeHtml(job.error || 'Не удалось озвучить файл')}</p>`;
  }
  return `
    <article class="queue-item ${active ? 'active' : ''}">
      <div class="file-row">
        <div class="file-mark">TXT</div>
        <div class="file-title"><strong>${escapeHtml(job.filename)}</strong><span>${job.characters.toLocaleString('ru-RU')} символов</span></div>
        <span class="status-pill ${job.state}">${stageNames[status] || stageNames[job.state]}</span>
      </div>
      <div class="progress-track" aria-label="Прогресс ${progress}%"><span style="width:${progress}%"></span></div>
      <div class="progress-caption"><span>${fragments}</span><strong>${progress}%</strong></div>
      ${result}
    </article>`;
}

async function refreshQueue() {
  try {
    const jobs = await getJson('/api/file-jobs');
    queueCount.textContent = `${jobs.length} ${jobs.length === 1 ? 'файл' : jobs.length < 5 ? 'файла' : 'файлов'}`;
    queueList.innerHTML = jobs.length
      ? jobs.map(cardTemplate).join('')
      : '<div class="empty-queue"><span>∿</span><p>Здесь появятся загруженные файлы и прогресс озвучки.</p></div>';
    const hasActive = jobs.some((job) => ['queued', 'processing'].includes(job.state));
    clearTimeout(pollTimer);
    pollTimer = hasActive ? setTimeout(refreshQueue, 1000) : null;
  } catch (error) {
    showError(error.message);
  }
}

fileInput.addEventListener('change', () => {
  const file = fileInput.files[0];
  $('#file-label').textContent = file ? file.name : 'Выберите TXT-файл';
  $('#file-hint').textContent = file
    ? `${formatBytes(file.size)} · готов к загрузке`
    : 'UTF-8 или Windows-1251 · до 2 МБ';
  $('#drop-zone').classList.toggle('selected', Boolean(file));
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  message.hidden = true;
  const file = fileInput.files[0];
  if (!file) return showError('Выберите TXT-файл.');
  const data = new FormData();
  data.append('file', file);
  data.append('voice', $('#file-voice').value);
  data.append('speed', $('#file-speed').value);
  data.append('sample_rate', $('#file-rate').value);
  data.append('auto_stress', $('#file-stress').checked ? 'true' : 'false');
  setLoading(true);
  try {
    await getJson('/api/file-jobs', { method: 'POST', body: data });
    form.reset();
    $('#file-label').textContent = 'Выберите TXT-файл';
    $('#file-hint').textContent = 'UTF-8 или Windows-1251 · до 2 МБ';
    $('#drop-zone').classList.remove('selected');
    await refreshQueue();
  } catch (error) {
    showError(error.message);
  } finally {
    setLoading(false);
  }
});

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

checkHealth();
refreshQueue();

