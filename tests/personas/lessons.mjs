// The course as one computer's student reads it, for the persona tests (tests/personas). Run from
// a checkout of the website (it uses the site's own lesson pipeline, src/lib/remark-callouts.mjs),
// copied into its scripts/ folder by .github/workflows/personas.yml:
//
//   node scripts/persona-lessons.mjs <mac|windows> <first module> <last module> > lessons.json
//
// Each module's page (what's new, what you'll need, its introduction), then its lessons in order,
// as plain text: callout boxes keep their titles ("Check it worked"), and only this computer's
// version of each step is there.
//
// And each module's video, as this computer's students see it (the site's own video scripts,
// video/modules/mNN/script.yaml, through video/scripts/lib/variants.mjs): scene by scene, what's
// on screen and what the voice says. A persona can't watch a video, so it reads this instead.
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { parse as parseYaml } from 'yaml';
import { resolve as resolveVideo } from '../video/scripts/lib/variants.mjs';
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkDirective from 'remark-directive';
import remarkRehype from 'remark-rehype';
import rehypeStringify from 'rehype-stringify';
import { visit } from 'unist-util-visit';
import remarkCallouts from '../src/lib/remark-callouts.mjs';
import { platformParts } from '../src/lib/platforms.mjs';

const [platform, first, last] = [process.argv[2], Number(process.argv[3] ?? 0), Number(process.argv[4] ?? 12)];
if (!['mac', 'windows'].includes(platform)) throw new Error('usage: persona-lessons.mjs <mac|windows> <first> <last>');
const other = platform === 'mac' ? 'windows' : 'mac';

// This computer's words stay, unwrapped; the other's go. Then the site's own plugin makes the boxes.
function onlyThisComputer() {
  return (tree) => {
    visit(tree, (node, index, parent) => {
      if ((node.type === 'containerDirective' || node.type === 'textDirective') && (node.name === platform || node.name === other)) {
        parent.children.splice(index, 1, ...(node.name === platform ? node.children : []));
        return index;
      }
    });
  };
}

const render = (markdown) => String(unified().use(remarkParse).use(remarkDirective).use(onlyThisComputer)
  .use(remarkCallouts, { windows: false }).use(remarkRehype, { allowDangerousHtml: true }).use(rehypeStringify, { allowDangerousHtml: true })
  .processSync(markdown));

// Numbered steps keep their numbers ("step 3"): each <li> of an <ol> gets the next one.
function numbered(html) {
  const stack = [];
  return html.replace(/<ol(?: start="(\d+)")?>|<\/ol>|<ul>|<\/ul>|<li>/g, (tag, start) => {
    if (tag.startsWith('<ol')) { stack.push(Number(start ?? 1)); return '\n'; }
    if (tag === '<ul>') { stack.push(null); return '\n'; }
    if (tag === '</ol>' || tag === '</ul>') { stack.pop(); return '\n'; }
    const n = stack[stack.length - 1];
    if (n === null || n === undefined) return '- ';
    stack[stack.length - 1] = n + 1;
    return `${n}. `;
  });
}

const text = (html) => numbered(html)
  .replace(/<p class="callout-title">([\s\S]*?)<\/p>/g, '\n[$1]\n')
  .replace(/<pre><code[^>]*>([\s\S]*?)<\/code><\/pre>/g, (_, code) => `\n\`\`\`\n${code.trim()}\n\`\`\`\n`)
  .replace(/<kbd>([\s\S]*?)<\/kbd>/g, '[$1]')
  .replace(/<code>([\s\S]*?)<\/code>/g, '`$1`')
  .replace(/<\/(p|li|h\d|aside|pre)>|<br\s*\/?>/g, '\n').replace(/<h(\d)[^>]*>/g, (_, n) => '#'.repeat(Number(n)) + ' ')
  .replace(/<[^>]+>/g, '').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#x27;|&#39;/g, "'").replace(/&amp;/g, '&')
  .replace(/\n{3,}/g, '\n\n').trim();

function split(path) {
  const raw = readFileSync(path, 'utf8');
  const m = /^---\n([\s\S]*?)\n---\n?([\s\S]*)$/.exec(raw);
  const data = {};
  let key = null;
  for (const line of (m?.[1] ?? '').split('\n')) {
    const item = /^ {2}- (.*)$/.exec(line);
    if (item && key) { data[key].push(item[1].replace(/^"|"$/g, '')); continue; }
    const kv = /^([a-z_]+):\s*(.*)$/.exec(line);
    if (kv) { key = kv[1]; data[key] = kv[2] === '' ? [] : kv[2].replace(/^"|"$/g, ''); }
  }
  return { data, body: m?.[2] ?? raw };
}
const words = (s) => platformParts(String(s ?? ''), true).filter((p) => p.for !== other).map((p) => p.text).join('');

// Every string inside a scene's drawing (a page, a dialog, a brief…), in order.
const strings = (v) => (typeof v === 'string' ? [v] : Array.isArray(v) ? v.flatMap(strings)
  : v && typeof v === 'object' ? Object.values(v).flatMap(strings) : []);

// What a terminal scene's recording shows being typed: the tape's Type lines after its Show (the
// lines before it replay hidden).
function typed(dir, tape) {
  const file = join(dir, 'tapes', tape);
  if (!tape || !existsSync(file)) return [];
  const lines = readFileSync(file, 'utf8').split('\n');
  const shown = lines.includes('Show') ? lines.slice(lines.indexOf('Show')) : lines;
  return shown.map((l) => /^Type(?:@\S+)?\s+(["`])(.*)\1\s*$/.exec(l.trim())?.[2]).filter(Boolean);
}

function scene(s, script, dir) {
  const said = String(s.narration ?? '').replace(/\s+/g, ' ').trim();
  let shown;
  if (s.show === 'title') shown = `A title card: ${script.title}. ${script.subtitle ?? ''}`.trim();
  else if (s.show === 'end') shown = ['The end card', [script.tagline, ...strings(script.next)].filter(Boolean).join(' ')].filter(Boolean).join(': ');
  else if (s.show === 'terminal') {
    const lines = typed(dir, s.tape);
    shown = 'A real recording of the Butler window' + (lines.length ? `, where someone types: ${lines.map((l) => `\`${l}\``).join(', ')}` : '');
  } else if (Array.isArray(s.chat)) {
    shown = `${s.callout ?? 'Claude Code'}: ` + s.chat.map((c) => (c.you ? `you type "${c.you}"` : c.claude ? `Claude says "${c.claude}"`
      : c.allow ? `Claude Code asks to: ${c.allow} (Allow)` : strings(c).join(' · '))).join('; ');
  } else {
    // Only the drawing this scene shows: a Windows scene that became a page keeps the Mac's dialog
    // data too, unshown.
    const drawn = strings(s[s.show]);
    shown = `${s.callout ?? s.show}${drawn.length ? `: ${drawn.join(' · ')}` : ''}`;
  }
  return { shown, said };
}

// A module's video on this computer: its title, about how long, and each scene.
function video(n) {
  const dir = join('video/modules', `m${String(n).padStart(2, '0')}`);
  if (!existsSync(join(dir, 'script.yaml'))) return null;
  const script = resolveVideo(parseYaml(readFileSync(join(dir, 'script.yaml'), 'utf8')), platform);
  const scenes = script.scenes.map((s) => scene(s, script, dir));
  const spoken = scenes.reduce((sum, s) => sum + s.said.split(' ').filter(Boolean).length, 0);
  return { module: n, title: script.title, minutes: Math.max(1, Math.round((spoken / 2.6 + 1.5 * scenes.length) / 60)), scenes };
}

const out = [];
const videos = {};
const modules = readdirSync('src/content/modules').filter((f) => f.endsWith('.md'))
  .map((f) => split(join('src/content/modules', f))).filter((m) => Number(m.data.number) >= first && Number(m.data.number) <= last)
  .sort((a, b) => Number(a.data.number) - Number(b.data.number));
for (const mod of modules) {
  const n = Number(mod.data.number);
  const needs = (Array.isArray(mod.data.needs) ? mod.data.needs : []).map(words).filter(Boolean);
  const v = video(n);
  if (v) videos[n] = v;
  out.push({
    module: n, order: 0, id: `module-${n}`, title: `Module ${n}: ${mod.data.title}`,
    text: [`# Module ${n}: ${mod.data.title}`,
      ...(v ? [`[The page opens with this module's video: ${v.title}, about ${v.minutes} minutes.]`] : []),
      String(mod.data.win ?? ''), `New this module: ${words(mod.data.setup)}`,
      `You'll need:\n${needs.map((x) => `- ${x}`).join('\n')}`, text(render(mod.body))].join('\n\n'),
  });
  const dir = join('src/content/lessons', `m${String(n).padStart(2, '0')}`);
  const lessons = readdirSync(dir).filter((f) => f.endsWith('.md')).map((f) => split(join(dir, f)))
    .sort((a, b) => Number(a.data.order) - Number(b.data.order));
  for (const l of lessons) {
    out.push({ module: n, order: Number(l.data.order), id: l.data.id, title: `Lesson ${n}.${l.data.order}: ${l.data.title}`,
      text: `# Lesson ${n}.${l.data.order}: ${l.data.title} (${l.data.time})\n\n${text(render(l.body))}` });
  }
}
process.stdout.write(JSON.stringify({ platform, lessons: out, videos }, null, 2) + '\n');
