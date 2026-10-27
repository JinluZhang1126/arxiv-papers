/* Date navigation reads the existing table; no data or history is rewritten. */
(() => {
  'use strict';
  function init() {
    const groups = new Map();
    document.querySelectorAll('table tbody tr').forEach(row => {
      const date = row.cells[0]?.textContent.trim().match(/^\d{4}-\d{2}-\d{2}$/)?.[0];
      if (date) {
        if (!groups.has(date)) groups.set(date, []);
        groups.get(date).push(row);
      }
    });
    if (!groups.size) return;
    const nav = document.createElement('nav');
    nav.className = 'date-index';
    nav.setAttribute('aria-label', '论文日期索引');
    const title = document.createElement('strong');
    title.textContent = '日期索引';
    nav.append(title);
    const reset = document.createElement('button');
    reset.type = 'button'; reset.textContent = '全部日期'; nav.append(reset);
    const list = document.createElement('div'); list.className = 'date-index-list'; nav.append(list);
    const buttons = [];
    function select(date) {
      groups.forEach((rows, key) => rows.forEach(row => { row.hidden = date !== null && key !== date; }));
      buttons.forEach(([key, button]) => button.setAttribute('aria-pressed', String(key === date)));
      reset.setAttribute('aria-pressed', String(date === null));
      document.querySelectorAll('table').forEach(table => {
        const rows = Array.from(table.querySelectorAll('tbody tr'));
        const empty = rows.length > 0 && rows.every(row => row.hidden);
        table.hidden = empty;
        const heading = table.previousElementSibling;
        if (heading && /^H[23]$/.test(heading.tagName)) heading.hidden = empty;
      });
      if (date !== null) groups.get(date)[0].scrollIntoView({behavior: 'smooth', block: 'start'});
    }
    reset.addEventListener('click', () => select(null));
    Array.from(groups.keys()).sort().reverse().forEach(date => {
      const button = document.createElement('button');
      button.type = 'button'; button.textContent = date + ' · ' + groups.get(date).length;
      button.addEventListener('click', () => select(date));
      buttons.push([date, button]); list.append(button);
    });
    document.body.append(nav); document.body.classList.add('has-date-index'); select(null);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
