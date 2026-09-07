const form = document.querySelector('#create-form');
const input = document.querySelector('#new-title');
const list = document.querySelector('#todos');
const errorBox = document.querySelector('#error');

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: options.body ? {'Content-Type': 'application/json'} : undefined,
  });
  if (response.status === 204) return null;
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || 'Request failed');
  return payload;
}

function showError(error) {
  errorBox.textContent = error ? String(error.message || error) : '';
}

function renderTodo(todo) {
  const item = document.createElement('li');
  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.checked = todo.completed;
  checkbox.setAttribute('aria-label', `Mark ${todo.title} complete`);

  const title = document.createElement('span');
  title.textContent = todo.title;
  if (todo.completed) title.classList.add('completed');

  const edit = document.createElement('button');
  edit.textContent = 'Edit';
  const remove = document.createElement('button');
  remove.textContent = 'Delete';

  checkbox.addEventListener('change', async () => {
    try {
      await api(`/api/todos/${todo.id}`, {method: 'PATCH', body: JSON.stringify({completed: checkbox.checked})});
      await loadTodos();
    } catch (error) { showError(error); }
  });

  edit.addEventListener('click', async () => {
    const nextTitle = window.prompt('Edit todo', todo.title);
    if (nextTitle === null) return;
    try {
      await api(`/api/todos/${todo.id}`, {method: 'PATCH', body: JSON.stringify({title: nextTitle})});
      await loadTodos();
    } catch (error) { showError(error); }
  });

  remove.addEventListener('click', async () => {
    try {
      await api(`/api/todos/${todo.id}`, {method: 'DELETE'});
      await loadTodos();
    } catch (error) { showError(error); }
  });

  item.append(checkbox, title, edit, remove);
  return item;
}

async function loadTodos() {
  showError(null);
  const todos = await api('/api/todos');
  list.replaceChildren(...todos.map(renderTodo));
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await api('/api/todos', {method: 'POST', body: JSON.stringify({title: input.value})});
    input.value = '';
    await loadTodos();
  } catch (error) { showError(error); }
});

loadTodos().catch(showError);
