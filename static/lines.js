/* What someone wants, one thing per line, each with an optional backup in
   case the restaurant is out of it. Shared by the ordering page and the
   organiser's form so the two behave exactly the same.

   There are always at least two lines and always one empty line at the
   bottom, so a third appears the moment the second is used. The backup box
   shows under the first line from the start -- nobody should have to discover
   that it exists -- and under any other line once something is typed there.

   Everything goes in as textContent: coworkers type all of it. Wrapped so
   the page gains exactly one name, ItemLines. Both page scripts declare their
   own top-level $ and el, and a second declaration of either would stop the
   whole page from running. */
const ItemLines = (() => {
  const START = 2;     // lines on an empty form
  const MOST = 10;     // the server refuses more than this from the public page

  const node = (tag, cls, text) => {
    const made = document.createElement(tag);
    if (cls) made.className = cls;
    if (text != null) made.textContent = text;
    return made;
  };

  // Words worth comparing: enough to tell "rice pl" belongs with "Rice plate
  // with lamb" and "Veggie wrap" does not.
  const words = (text) => new Set(text.toLowerCase().split(/[^a-z0-9]+/)
                                      .filter((word) => word.length > 2));

  function create(host, options = {}) {
    const lines = [];
    let last = null;          // the line most recently typed in

    const filled = (line) => line.item.value.trim() !== "";
    const used = (line) => filled(line) || line.alt.value.trim() !== "";
    const filledLines = () => lines.filter(filled);

    function build() {
      const row = node("div", "itemLine");
      const number = node("span", "lineNo");
      const item = node("input", "lineItem");
      item.autocomplete = "off";
      const backup = node("label", "lineBackup");
      const alt = node("input", "lineAlt");
      alt.autocomplete = "off";
      alt.placeholder = "backup (optional)";
      if (options.list) {
        item.setAttribute("list", options.list);
        alt.setAttribute("list", options.list);
      }
      backup.append(node("span", null, "If they're out"), alt);
      row.append(number, item, backup);

      const line = { row, number, item, alt, backup };
      item.addEventListener("input", tidy);
      alt.addEventListener("input", tidy);
      item.addEventListener("focus", () => { last = line; });
      return line;
    }

    function add() {
      const line = build();
      lines.push(line);
      host.append(line.row);
      return line;
    }

    /* One empty line at the bottom, always: add one when the last is used,
       and drop spare empty ones -- but never the line being typed in, which
       would pull the box out from under the cursor. */
    function tidy() {
      while (lines.length > START) {
        const end = lines[lines.length - 1];
        const before = lines[lines.length - 2];
        if (used(end) || used(before) || end.row.contains(document.activeElement)) break;
        end.row.remove();
        lines.pop();
        if (last === end) last = null;
      }
      if (used(lines[lines.length - 1]) && lines.length < MOST) add();
      lines.forEach((line, index) => {
        line.number.textContent = String(index + 1);
        line.item.placeholder = index === 0 ? (options.first || "") : (options.more || "");
        line.backup.classList.toggle("hidden", index > 0 && !used(line));
        line.item.setAttribute("aria-label", `Item ${index + 1}`);
        line.alt.setAttribute("aria-label", `Backup for item ${index + 1}`);
      });
    }

    /* Everything filled in, in order. A line with only a backup and nothing
       to back up is left out -- it means nothing. */
    function values() {
      return filledLines().map((line) => ({ desc: line.item.value.trim(),
                                            fallback: line.alt.value.trim() }));
    }

    function clear() {
      for (const line of lines) line.row.remove();
      lines.length = 0;
      last = null;
      for (let i = 0; i < START; i++) add();
      tidy();
    }

    /* A tapped "what others ordered" chip. It replaces the line being typed
       when that line is heading for the same dish ("rice pl"), and otherwise
       goes on the first empty line, so tapping a second dish never wipes the
       first. */
    function fill(desc) {
      const current = last && lines.includes(last) ? last : null;
      const sameDish = current && filled(current)
        && [...words(current.item.value)].some((word) => words(desc).has(word));
      const target = current && (!filled(current) || sameDish)
        ? current
        : lines.find((line) => !filled(line)) || lines[lines.length - 1];
      target.item.value = desc;
      tidy();
      target.item.focus();
    }

    /* The index is into values(): the n-th line that has something in it. */
    function setDesc(index, text) {
      const line = filledLines()[index];
      if (!line) return;
      line.item.value = text;
      tidy();
    }

    /* Marks the line a question is about. Not focused: on a phone that would
       raise the keyboard over the very question being asked. */
    function highlight(index) {
      const target = index >= 0 ? filledLines()[index] : null;
      for (const line of lines) line.row.classList.toggle("lineAsk", line === target);
    }

    function focus() {
      (lines.find((line) => !filled(line)) || lines[0]).item.focus();
    }

    clear();
    return { values, clear, fill, setDesc, highlight, focus };
  }

  return { create };
})();
