'use strict';
// A deliberately small DOM adapter for offline behavior tests; not a browser/layout substitute.
class Element {
  constructor(tag = 'div') { this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.attributes = {}; this.listeners = {}; this.value = ''; this.disabled = false; this.hidden = false; this.className = ''; this._text = ''; this.style = { setProperty: (key, value) => { this.style[key] = value; } }; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set innerHTML(_) { throw new Error('Unsafe innerHTML insertion is prohibited'); }
  append(...children) { for (const child of children) this.children.push(typeof child === 'string' ? Object.assign(new Element('span'), { textContent: child }) : child); }
  replaceChildren(...children) { this._text = ''; this.children = []; this.append(...children); }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key]; }
  addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
  async emit(type, properties = {}) { for (const callback of this.listeners[type] || []) await callback({ preventDefault() {}, target: this, ...properties }); }
  focus() { this.focused = true; }
  get classList() { return { toggle: (token, force) => { const classes = new Set(this.className.split(' ').filter(Boolean)); if (force === undefined ? !classes.has(token) : force) classes.add(token); else classes.delete(token); this.className = [...classes].join(' '); } }; }
}
class Document {
  constructor(html) { this.elements = new Map([...html.matchAll(/<([a-z][\w-]*)\b[^>]*\bid="([^"]+)"[^>]*>/gi)].map(match => [match[2], new Element(match[1])])); this.title = ''; }
  getElementById(id) { if (!this.elements.has(id)) throw new Error(`Missing HTML element #${id}`); return this.elements.get(id); }
  createElement(tag) { return new Element(tag); }
}
function descendants(element) { return [element, ...element.children.flatMap(descendants)]; }
module.exports = { Document, descendants };
