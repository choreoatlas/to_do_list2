const form = document.querySelector('#todo-form');
const input = document.querySelector('#new-title');
const list = document.querySelector('#todo-list');
const empty = document.querySelector('#empty-state');
const error = document.querySelector('#error');

function showError(message) {
  error.textContent = message;
  error.hidden = !message;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  if (response.status === 204) return null;
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || 'Request failed');
  return payload;
}

function renderTodo(todo) {
  const li = document.createElement('li');
  li.className = `todo${todo.completed ? ' completed' : ''}`;

  const check = document.createElement('input');
  check.type = 'checkbox';
  check.checked = todo.completed;
  check.setAttribute('aria-label', `Mark ${todo.title} ${todo.completed ? 'incomplete' : 'complete'}`);
  check.addEventListener('change', async () => {
    try {
      showError('');
      await api(`/api/todos/${todo.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ completed: check.checked }),
      });
      await loadTodos();
    } catch (err) {
      check.checked = !check.checked;
      showError(err.message);
    }
  });

  const title = document.createElement('button');
  title.type = 'button';
  title.className = 'todo-title';
  title.textContent = todo.title;
  title.title = 'Click to edit';
  title.addEventListener('click', async () => {
    const next = window.prompt('Edit todo', todo.title);
    if (next === null) return;
    try {
      showError('');
      await api(`/api/todos/${todo.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ title: next }),
      });
      await loadTodos();
    } catch (err) {
      showError(err.message);
    }
  });

  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'delete-button';
  remove.setAttribute('aria-label', `Delete ${todo.title}`);
  remove.textContent = 'Delete';
  remove.addEventListener('click', async () => {
    try {
      showError('');
      await api(`/api/todos/${todo.id}`, { method: 'DELETE' });
      await loadTodos();
    } catch (err) {
      showError(err.message);
    }
  });

  li.append(check, title, remove);
  return li;
}

async function loadTodos() {
  try {
    showError('');
    const todos = await api('/api/todos');
    list.replaceChildren(...todos.map(renderTodo));
    empty.hidden = todos.length > 0;
  } catch (err) {
    showError(err.message);
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    showError('');
    await api('/api/todos', {
      method: 'POST',
      body: JSON.stringify({ title: input.value }),
    });
    input.value = '';
    input.focus();
    await loadTodos();
  } catch (err) {
    showError(err.message);
  }
});

loadTodos();
