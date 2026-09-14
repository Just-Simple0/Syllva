import { promises as fs } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const siteRoot = path.resolve(scriptDir, '..');
const repoRoot = path.resolve(siteRoot, '..');
const generatedRoot = path.join(siteRoot, 'src', 'content', 'docs');
const checkOnly = process.argv.includes('--check');
const repositoryBlob = 'https://github.com/Just-Simple0/Syllva/blob/main';

const sections = ['user-guide', 'operator-guide', 'concepts', 'reference'];
const order = {
  'user-guide': ['README', 'getting-started', 'daily-use', 'studying-with-ai', 'mcp-and-clients', 'troubleshooting'],
  'operator-guide': ['README', 'installation', 'configuration', 'intake-and-notion', 'mcp-clients', 'lms-sync', 'operations'],
  concepts: ['README', 'architecture', 'trust-model'],
  reference: ['README', 'cli', 'statuses', 'mcp-tools'],
};

async function walk(directory) {
  const entries = await fs.readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) files.push(...(await walk(full)));
    else if (entry.isFile() && entry.name.endsWith('.md')) files.push(full);
  }
  return files;
}

function posixRelative(file) {
  return path.relative(repoRoot, file).split(path.sep).join('/');
}

function destinationFor(section, sourcePath) {
  const sectionRoot = `docs/${section}/`;
  let relative = sourcePath.slice(sectionRoot.length);
  const korean = relative.endsWith('.ko.md');
  relative = korean ? relative.slice(0, -'.ko.md'.length) + '.md' : relative;
  if (path.posix.basename(relative) === 'README.md') {
    relative = path.posix.join(path.posix.dirname(relative), 'index.md');
  }
  return korean ? path.posix.join('ko', section, relative) : path.posix.join(section, relative);
}

function englishPeer(sourcePath) {
  return sourcePath.endsWith('.ko.md') ? sourcePath.slice(0, -'.ko.md'.length) + '.md' : null;
}

function koreanPeer(sourcePath) {
  return sourcePath.endsWith('.md') && !sourcePath.endsWith('.ko.md')
    ? sourcePath.slice(0, -'.md'.length) + '.ko.md'
    : null;
}

function routeFor(destination) {
  return destination.replace(/\.md$/, '').replace(/\/index$/, '');
}

function relativeRoute(currentDestination, targetDestination) {
  const currentRoute = routeFor(currentDestination);
  const targetRoute = routeFor(targetDestination);
  const relative = path.posix.relative(currentRoute, targetRoute) || '.';
  return `${relative}/`;
}

function titleAndBody(markdown, sourcePath) {
  const normalized = markdown.replace(/\r\n/g, '\n');
  const match = normalized.match(/^#\s+(.+)\s*$/m);
  if (!match) throw new Error(`Missing H1 title in ${sourcePath}`);
  const title = match[1].trim();
  let body = normalized.replace(match[0], '');
  body = body.replace(/^\[(?:한국어|English)\]\([^)]+\)\s*$/gm, '');
  return { title, body: body.trimStart() };
}

function rewriteLinks(body, sourcePath, destination, mapping) {
  return body.replace(/\]\(([^)\s]+)\)/g, (full, rawTarget) => {
    if (/^(?:https?:|mailto:|#|\/)/.test(rawTarget)) return full;

    const hashIndex = rawTarget.indexOf('#');
    const targetPath = hashIndex >= 0 ? rawTarget.slice(0, hashIndex) : rawTarget;
    const hash = hashIndex >= 0 ? rawTarget.slice(hashIndex) : '';
    if (!targetPath.endsWith('.md')) return full;

    const resolved = path.posix.normalize(path.posix.join(path.posix.dirname(sourcePath), targetPath));
    const mapped = mapping.get(resolved);
    if (mapped) return `](${relativeRoute(destination, mapped)}${hash})`;
    return `](${repositoryBlob}/${resolved}${hash})`;
  });
}

function sidebarOrder(section, sourcePath) {
  let name = path.posix.basename(sourcePath);
  name = name.replace(/\.ko\.md$/, '').replace(/\.md$/, '');
  const value = order[section]?.indexOf(name) ?? -1;
  return value >= 0 ? value : 100;
}

function renderPage(title, body, sidebarIndex) {
  return [
    '---',
    `title: ${JSON.stringify(title)}`,
    'sidebar:',
    `  order: ${sidebarIndex}`,
    '---',
    '',
    body.trimEnd(),
    '',
  ].join('\n');
}

const sources = [];
for (const section of sections) {
  const directory = path.join(repoRoot, 'docs', section);
  for (const file of await walk(directory)) {
    sources.push({ section, file, sourcePath: posixRelative(file) });
  }
}

const sourceSet = new Set(sources.map((item) => item.sourcePath));
const missingPairs = [];
for (const item of sources) {
  const peer = englishPeer(item.sourcePath) ?? koreanPeer(item.sourcePath);
  if (peer && !sourceSet.has(peer)) missingPairs.push(`${item.sourcePath} -> ${peer}`);
}
if (missingPairs.length) {
  throw new Error(`Missing EN/KO public documentation pairs:\n${missingPairs.join('\n')}`);
}

const mapping = new Map();
for (const item of sources) {
  const destination = destinationFor(item.section, item.sourcePath);
  if ([...mapping.values()].includes(destination)) throw new Error(`Duplicate destination: ${destination}`);
  mapping.set(item.sourcePath, destination);
}

if (checkOnly) {
  console.log(`Validated ${sources.length} bilingual public documentation source files.`);
  process.exit(0);
}

await fs.rm(generatedRoot, { recursive: true, force: true });
await fs.mkdir(generatedRoot, { recursive: true });

const homepagePairs = [
  ['source/index.md', 'index.md'],
  ['source/index.ko.md', 'ko/index.md'],
];
for (const [source, destination] of homepagePairs) {
  const input = await fs.readFile(path.join(siteRoot, source), 'utf8');
  const output = path.join(generatedRoot, destination);
  await fs.mkdir(path.dirname(output), { recursive: true });
  await fs.writeFile(output, input, 'utf8');
}

for (const item of sources) {
  const destination = mapping.get(item.sourcePath);
  const raw = await fs.readFile(item.file, 'utf8');
  const { title, body } = titleAndBody(raw, item.sourcePath);
  const rewritten = rewriteLinks(body, item.sourcePath, destination, mapping);
  const rendered = renderPage(title, rewritten, sidebarOrder(item.section, item.sourcePath));
  const output = path.join(generatedRoot, destination);
  await fs.mkdir(path.dirname(output), { recursive: true });
  await fs.writeFile(output, rendered, 'utf8');
}

console.log(`Generated ${sources.length + homepagePairs.length} Starlight pages from repository documentation.`);
