/*
 * Java code colors for LearnHouse activity pages.
 *
 * Paste this whole file into Organization settings, Other, Scripts.
 *
 * What it does
 * - Colors every code block on dynamic pages as Java, with a different color
 *   for each kind of word: control keywords, modifiers, primitive types,
 *   classes, methods, variables, text, numbers, comments.
 * - Hover a word (or tap it on a phone) to see what it is, for example
 *   "int: primitive type for whole numbers".
 * - Blocks that are not code (program output) are shown in one plain color.
 * - Blocks set to another language (Python, SQL...) are left as they are.
 *
 * It only paints colors over the text (CSS Custom Highlight API). It never
 * changes the page or the lesson content, so it is safe in the editor too.
 * Browsers without that feature simply keep the old colors.
 *
 * Note for editing this file: the Scripts box turns three characters into
 * HTML codes (less than, greater than, ampersand), which breaks JavaScript.
 * This file therefore never uses them: comparisons go through isLess() and
 * "and" conditions are written as nested ifs.
 */
(function () {
  if (typeof window === 'undefined') return;
  if (window.__lhJavaColors) return;
  if (!window.CSS) return;
  if (!CSS.highlights) return;
  if (typeof Highlight === 'undefined') return;
  window.__lhJavaColors = true;

  /* ---- Settings you may change ----------------------------------------- */

  var SHOW_HINTS = true;

  var COLORS = {
    control: '#c586c0',    /* if, for, return, new ... */
    modifier: '#569cd6',   /* public, static, class ... */
    primitive: '#ff8fa3',  /* int, double, boolean, void ... */
    type: '#4ec9b0',       /* String, Scanner, your own classes */
    method: '#dcdcaa',     /* println(...), increment(...) */
    variable: '#9cdcfe',   /* x, n1, count */
    string: '#98c379',     /* "text" and 'c' */
    number: '#d19a66',     /* 42, 3.14, true, false, null */
    comment: '#7f848e',    /* comments */
    annotation: '#b4b4b4', /* @Override */
    op: '#d4d4d4',         /* = + ; ( ) { } */
    output: '#d0d0d0'      /* blocks that are program output */
  };

  /* ---- Java words ------------------------------------------------------- */

  /* Lookup tables have no prototype, so a Java word like toString or
     constructor never matches JavaScript's own object methods. */
  function table(source) {
    var t = Object.create(null);
    Object.keys(source).forEach(function (k) { t[k] = source[k]; });
    return t;
  }

  function words(list) {
    var set = Object.create(null);
    list.split(' ').forEach(function (w) { set[w] = true; });
    return set;
  }

  var CONTROL = words('if else for while do switch case default break continue return try catch finally throw throws new instanceof this super assert yield');
  var MODIFIER = words('public private protected static final abstract class interface enum extends implements import package record var synchronized native transient volatile strictfp sealed permits');
  var PRIMITIVE = words('int double boolean char long float short byte void');
  var LITERAL = words('true false null');

  /* Short explanations for the words students meet first. */
  var NOTES = table({
    'public': 'visible to every other class',
    'private': 'only visible inside this class',
    'protected': 'visible to this class, its package and its subclasses',
    'static': 'belongs to the class itself, not to one object',
    'final': 'cannot be changed once it is set',
    'class': 'defines a new class',
    'interface': 'defines a list of methods a class promises to have',
    'extends': 'inherits from another class',
    'implements': 'promises to provide the methods of an interface',
    'import': 'makes a class from another package available',
    'package': 'names the folder group this file belongs to',
    'void': 'this method returns nothing',
    'int': 'whole numbers, like 7 or -3',
    'double': 'decimal numbers, like 3.14',
    'boolean': 'true or false',
    'char': 'one character, like \'a\'',
    'long': 'very large whole numbers',
    'float': 'decimal numbers, less precise than double',
    'short': 'small whole numbers',
    'byte': 'very small whole numbers',
    'if': 'runs the next block only when the condition is true',
    'else': 'runs when the if condition is false',
    'for': 'repeats a block, usually a set number of times',
    'while': 'repeats a block while the condition is true',
    'do': 'runs a block once, then repeats it while the condition is true',
    'switch': 'chooses a case based on a value',
    'case': 'one choice inside a switch',
    'default': 'the choice used when no case matches',
    'break': 'leaves the loop or switch right away',
    'continue': 'skips to the next round of the loop',
    'return': 'ends the method and sends a value back',
    'new': 'creates a new object',
    'this': 'the current object',
    'super': 'the parent class',
    'try': 'runs code that might throw an exception',
    'catch': 'handles an exception thrown in the try block',
    'finally': 'always runs after try and catch',
    'throw': 'throws an exception',
    'throws': 'lists exceptions this method may throw',
    'true': 'boolean value',
    'false': 'boolean value',
    'null': 'no object at all',
    'String': 'holds text, like "hello"',
    'System': 'built in: the console and the running program',
    'Math': 'built-in math methods like Math.sqrt',
    'Scanner': 'reads input, for example from the keyboard',
    'ArrayList': 'a list that can grow and shrink',
    'Integer': 'the class version of int',
    'Double': 'the class version of double',
    'Boolean': 'the class version of boolean',
    'Character': 'the class version of char',
    'Object': 'the parent of every class',
    'out': 'the console output stream',
    'in': 'the console input stream',
    'println': 'prints the value, then moves to a new line',
    'print': 'prints the value and stays on the same line',
    'printf': 'prints formatted text',
    'main': 'where the program starts',
    'length': 'how many items or characters there are'
  });

  var KIND_LABEL = table({
    control: 'keyword',
    modifier: 'keyword',
    primitive: 'primitive type',
    type: 'class',
    variable: 'variable',
    string: 'text (String literal)',
    'char': 'character (char literal)',
    number: 'number',
    literal: 'value',
    comment: 'comment: Java ignores it',
    annotation: 'annotation: a note for the compiler',
    output: 'program output'
  });

  /* ---- Small helpers (no less-than or greater-than signs) --------------- */

  function isLess(a, b) { return a !== b ? Math.min(a, b) === a : false; }
  function within(v, low, high) { return Math.min(Math.max(v, low), high) === v; }

  var GT = String.fromCharCode(62);
  var SELECTOR = '.canva-content-wrapper .ProseMirror pre, .activity-editor-content-wrapper .ProseMirror pre';
  var OUTPUT_LANGS = words('text plaintext plain txt output console none');

  /* ---- Tokenizer -------------------------------------------------------- */

  function tokenize(text) {
    var tokens = [];
    var n = text.length;
    var i = 0;
    var prev = null;      /* last token that is not a comment */
    var beforePrev = null;

    function push(s, e, kind, note) {
      var tok = { s: s, e: e, kind: kind, text: text.slice(s, e), note: note || '' };
      tokens.push(tok);
      if (kind !== 'comment') { beforePrev = prev; prev = tok; }
      return tok;
    }

    function nextChar(from) {
      var j = from;
      while (j !== n) {
        if (!/\s/.test(text.charAt(j))) return text.charAt(j);
        j++;
      }
      return '';
    }

    function isTypeToken(t) {
      if (!t) return false;
      if (t.kind === 'primitive') return t.text !== 'void';
      if (t.kind === 'type') return true;
      if (t.text === ']') return true;
      if (t.text === GT) return beforePrev ? beforePrev.kind === 'type' : false;
      return false;
    }

    while (i !== n) {
      var c = text.charAt(i);
      var c2 = text.charAt(i + 1);

      if (/\s/.test(c)) { i++; continue; }

      if (c === '/') {
        if (c2 === '/') {
          var eol = text.indexOf('\n', i);
          var endLine = eol === -1 ? n : eol;
          push(i, endLine, 'comment');
          i = endLine;
          continue;
        }
        if (c2 === '*') {
          var close = text.indexOf('*/', i + 2);
          var endBlock = close === -1 ? n : close + 2;
          push(i, endBlock, 'comment');
          i = endBlock;
          continue;
        }
      }

      if (c === '"') {
        if (text.substr(i, 3) === '"""') {
          var tb = text.indexOf('"""', i + 3);
          var tbEnd = tb === -1 ? n : tb + 3;
          push(i, tbEnd, 'string');
          i = tbEnd;
          continue;
        }
        i = readQuoted(i, '"', 'string');
        continue;
      }

      if (c === '\'') {
        i = readQuoted(i, '\'', 'char');
        continue;
      }

      if (/[0-9]/.test(c) ? true : (c === '.' ? /[0-9]/.test(c2) : false)) {
        var j = i + 1;
        while (j !== n) {
          var d = text.charAt(j);
          if (/[0-9a-zA-Z_.]/.test(d)) { j++; continue; }
          if (d === '+' ? /[eE]/.test(text.charAt(j - 1)) : (d === '-' ? /[eE]/.test(text.charAt(j - 1)) : false)) { j++; continue; }
          break;
        }
        var num = text.slice(i, j);
        var decimal = /[.eEdDfF]/.test(num.replace(/^0[xX][0-9a-fA-F_]*/, ''));
        push(i, j, 'number', decimal ? 'decimal number (double)' : 'whole number (int)');
        i = j;
        continue;
      }

      if (c === '@') {
        if (/[A-Za-z_]/.test(c2)) {
          var a = i + 1;
          while (a !== n) {
            if (!/[A-Za-z0-9_$.]/.test(text.charAt(a))) break;
            a++;
          }
          push(i, a, 'annotation');
          i = a;
          continue;
        }
      }

      if (/[A-Za-z_$]/.test(c)) {
        var k = i + 1;
        while (k !== n) {
          if (!/[A-Za-z0-9_$]/.test(text.charAt(k))) break;
          k++;
        }
        var word = text.slice(i, k);
        var follows = nextChar(k);
        var afterDot = prev ? prev.text === '.' : false;

        if (afterDot) {
          if (follows === '(') push(i, k, 'method', 'method call');
          else if (/^[A-Z][A-Z0-9_]+$/.test(word)) push(i, k, 'variable', 'constant (a value that never changes)');
          else if (/^[A-Z]/.test(word)) push(i, k, 'type');
          else push(i, k, 'variable', 'field (a variable stored in an object or class)');
        } else if (CONTROL[word]) push(i, k, 'control');
        else if (MODIFIER[word]) push(i, k, 'modifier');
        else if (PRIMITIVE[word]) push(i, k, 'primitive');
        else if (LITERAL[word]) push(i, k, 'literal');
        else if (prev ? prev.text === 'new' : false) push(i, k, 'type', 'class: a new object of this class is created');
        else if (follows === '(') {
          push(i, k, 'method', isTypeToken(prev) ? 'method declaration: defines a new method' : (prev ? (prev.text === 'void' ? 'method declaration: defines a new method' : 'method call') : 'method call'));
        } else if (/^[A-Z][A-Z0-9_]+$/.test(word)) push(i, k, 'variable', 'constant (a value that never changes)');
        else if (/^[A-Z]/.test(word)) push(i, k, 'type');
        else if (isTypeToken(prev)) push(i, k, 'variable', 'variable declaration: creates the variable ' + word);
        else push(i, k, 'variable');
        i = k;
        continue;
      }

      push(i, i + 1, 'op');
      i++;
    }

    function readQuoted(start, quote, kind) {
      var q = start + 1;
      while (q !== n) {
        var ch = text.charAt(q);
        if (ch === '\\') {
          var escEnd = Math.min(q + 2, n);
          tokens.push({ s: q, e: escEnd, kind: 'escape', text: text.slice(q, escEnd), note: '' });
          q = escEnd;
          continue;
        }
        if (ch === quote) { q++; break; }
        if (ch === '\n') break;
        q++;
      }
      push(start, q, kind);
      return q;
    }

    return tokens;
  }

  /* ---- Which blocks are Java ------------------------------------------- */

  function blockMode(pre) {
    var code = pre.querySelector('code') || pre;
    var lang = '';
    code.classList.forEach(function (cls) {
      if (cls.indexOf('language-') === 0) lang = cls.slice(9).toLowerCase();
    });
    if (lang === 'java') return 'java';
    if (OUTPUT_LANGS[lang]) return 'output';
    if (lang) return 'skip';
    var text = code.textContent || '';
    if (/[;{}]/.test(text)) return 'java';
    if (/\b(public|private|static|void|class|import|int|double|boolean|String)\s+[A-Za-z_]/.test(text)) return 'java';
    return 'output';
  }

  /* ---- Painting --------------------------------------------------------- */

  var blocks = new WeakMap();
  var KINDS = ['control', 'modifier', 'primitive', 'type', 'method', 'variable', 'string', 'number', 'comment', 'annotation', 'op', 'output', 'escape'];

  function colorFor(kind) {
    if (kind === 'char') return 'string';
    if (kind === 'literal') return 'number';
    return kind;
  }

  function textMap(pre) {
    var code = pre.querySelector('code') || pre;
    var walker = document.createTreeWalker(code, NodeFilter.SHOW_TEXT);
    var parts = [];
    var text = '';
    var node = walker.nextNode();
    while (node) {
      parts.push({ node: node, start: text.length, len: node.data.length });
      text += node.data;
      node = walker.nextNode();
    }
    return { text: text, parts: parts };
  }

  function rangeFor(parts, s, e) {
    var range = document.createRange();
    var startSet = false;
    for (var p = 0; p !== parts.length; p++) {
      var part = parts[p];
      if (!startSet) {
        if (within(s - part.start, 0, part.len - 1)) {
          range.setStart(part.node, s - part.start);
          startSet = true;
        }
      }
      if (startSet) {
        if (within(e - part.start, 1, part.len)) {
          range.setEnd(part.node, e - part.start);
          return range;
        }
      }
    }
    return null;
  }

  function paint() {
    var sets = {};
    KINDS.forEach(function (k) { sets[k] = []; });

    document.querySelectorAll(SELECTOR).forEach(function (pre) {
      var mode = blockMode(pre);
      if (mode === 'skip') { blocks.delete(pre); return; }
      var map = textMap(pre);
      var tokens;
      if (mode === 'java') {
        tokens = tokenize(map.text);
      } else {
        tokens = [];
        var re = /\S+(?:[ \t]+\S+)*/g;
        var m = re.exec(map.text);
        while (m) {
          tokens.push({ s: m.index, e: m.index + m[0].length, kind: 'output', text: m[0], note: '' });
          m = re.exec(map.text);
        }
      }
      tokens.forEach(function (tok) {
        var r = rangeFor(map.parts, tok.s, tok.e);
        if (r) { tok.range = r; sets[colorFor(tok.kind)].push(r); }
      });
      blocks.set(pre, { tokens: tokens, parts: map.parts });
    });

    KINDS.forEach(function (k) {
      var h = new Highlight();
      sets[k].forEach(function (r) { h.add(r); });
      if (k === 'escape') h.priority = 1;
      CSS.highlights.set('lhj-' + k, h);
    });
  }

  function addStyles() {
    var css = '';
    Object.keys(COLORS).forEach(function (k) {
      css += '::highlight(lhj-' + k + ') { color: ' + COLORS[k] + '; }\n';
    });
    css += '::highlight(lhj-escape) { color: ' + COLORS.string + '; text-decoration: underline dotted; }\n';
    var style = document.createElement('style');
    style.id = 'lhj-java-colors';
    style.textContent = css;
    document.head.appendChild(style);
  }

  /* ---- Keep up with page changes --------------------------------------- */

  var timer = null;
  function schedule() {
    if (timer) return;
    timer = setTimeout(function () { timer = null; paint(); }, 120);
  }

  function touchesCode(m) {
    var el = m.target.nodeType === 1 ? m.target : m.target.parentElement;
    if (el ? el.closest(SELECTOR) : false) return true;
    var lists = [m.addedNodes, m.removedNodes];
    for (var l = 0; l !== 2; l++) {
      var list = lists[l];
      for (var x = 0; x !== list.length; x++) {
        var node = list[x];
        if (node.nodeType === 1) {
          if (node.matches(SELECTOR)) return true;
          if (node.querySelector('pre')) return true;
        }
      }
    }
    return false;
  }

  new MutationObserver(function (mutations) {
    for (var q = 0; q !== mutations.length; q++) {
      if (touchesCode(mutations[q])) { schedule(); return; }
    }
  }).observe(document.body, { childList: true, subtree: true, characterData: true });

  /* ---- Hints on hover and tap ------------------------------------------ */

  var tip = null;
  var hideTimer = null;

  function describe(tok) {
    var word = tok.text;
    var label;
    if (tok.kind === 'method') label = tok.note;
    else if (tok.kind === 'number') label = tok.note;
    else if (tok.kind === 'variable') label = tok.note || 'variable';
    else if (tok.kind === 'type') label = tok.note || KIND_LABEL.type;
    else label = KIND_LABEL[tok.kind] || '';
    var note = NOTES[word];
    if (tok.kind === 'method') note = NOTES[word] || '';
    if (tok.kind === 'string') return 'text (String literal)';
    if (tok.kind === 'char') return 'one character (char literal)';
    if (tok.kind === 'comment') return KIND_LABEL.comment;
    if (tok.kind === 'output') return KIND_LABEL.output;
    if (tok.kind === 'escape') return word + ': escape sequence' + (word === '\\n' ? ', a new line' : (word === '\\t' ? ', a tab' : ''));
    var head = word.length === 0 ? '' : word + ': ';
    if (note) return head + label + ' \u2014 ' + note;
    return head + label;
  }

  function caretAt(x, y) {
    if (document.caretPositionFromPoint) {
      var p = document.caretPositionFromPoint(x, y);
      return p ? { node: p.offsetNode, offset: p.offset } : null;
    }
    if (document.caretRangeFromPoint) {
      var r = document.caretRangeFromPoint(x, y);
      return r ? { node: r.startContainer, offset: r.startOffset } : null;
    }
    return null;
  }

  function hit(range, x, y) {
    var rects = range.getClientRects();
    for (var z = 0; z !== rects.length; z++) {
      var b = rects[z];
      if (within(x, b.left, b.right)) {
        if (within(y, b.top, b.bottom)) return b;
      }
    }
    return null;
  }

  function tokenAt(x, y) {
    var target = document.elementFromPoint(x, y);
    var pre = target ? target.closest('pre') : null;
    var data = pre ? blocks.get(pre) : null;
    if (!data) return null;
    var pos = caretAt(x, y);
    if (!pos) return null;
    var flat = -1;
    data.parts.forEach(function (part) {
      if (part.node === pos.node) flat = part.start + pos.offset;
    });
    if (flat === -1) return null;
    var candidates = [flat, flat - 1];
    for (var t = 0; t !== data.tokens.length; t++) {
      var tok = data.tokens[t];
      if (tok.kind === 'op') continue;
      if (!tok.range) continue;
      for (var u = 0; u !== 2; u++) {
        if (within(candidates[u], tok.s, tok.e - 1)) {
          var box = hit(tok.range, x, y);
          if (box) return { tok: tok, box: box };
        }
      }
    }
    return null;
  }

  function ensureTip() {
    if (tip) return tip;
    tip = document.createElement('div');
    tip.setAttribute('role', 'tooltip');
    var s = tip.style;
    s.position = 'fixed';
    s.zIndex = '2147483000';
    s.pointerEvents = 'none';
    s.maxWidth = '320px';
    s.padding = '6px 10px';
    s.borderRadius = '8px';
    s.background = '#1f1f1f';
    s.color = '#f5f5f5';
    s.font = '500 12px/1.45 system-ui, -apple-system, Segoe UI, sans-serif';
    s.boxShadow = '0 4px 16px rgba(0,0,0,0.35)';
    s.border = '1px solid rgba(255,255,255,0.08)';
    s.display = 'none';
    document.body.appendChild(tip);
    return tip;
  }

  function show(found) {
    var el = ensureTip();
    el.textContent = describe(found.tok);
    el.style.display = 'block';
    var w = el.offsetWidth;
    var h = el.offsetHeight;
    var left = Math.max(8, Math.min(found.box.left, window.innerWidth - w - 8));
    var top = found.box.bottom + 6;
    if (isLess(window.innerHeight - 8, top + h)) top = found.box.top - h - 6;
    el.style.left = left + 'px';
    el.style.top = top + 'px';
  }

  function hide() {
    if (tip) tip.style.display = 'none';
  }

  if (SHOW_HINTS) {
    document.addEventListener('mousemove', function (e) {
      var found = tokenAt(e.clientX, e.clientY);
      if (found) show(found); else hide();
    }, { passive: true });
    document.addEventListener('click', function (e) {
      var found = tokenAt(e.clientX, e.clientY);
      if (!found) return;
      show(found);
      clearTimeout(hideTimer);
      hideTimer = setTimeout(hide, 3500);
    });
    window.addEventListener('scroll', hide, { passive: true, capture: true });
  }

  addStyles();
  paint();
})();
