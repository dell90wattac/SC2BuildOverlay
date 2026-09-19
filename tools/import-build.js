/**
 * Converts a Vespene.gg build export into a build-order file.
 *
 *   node tools/import-build.js <export.json> [options]
 *
 *   --list                 show the branches and exit
 *   --branch=<id>          which branch to convert (default: the main line)
 *   --slot=<1-9>           hotkey slot to write into the header
 *   --out=<name.txt>       filename under builds/ (default: derived from name)
 *   --drop-filler          leave repeated production out entirely
 *   --no-situational       leave out steps pros only sometimes build
 *   --notes / --no-notes   keep or drop the site's English coaching notes
 *                          (default: drop — they are long English sentences)
 *   --min-freq=<0-1>       frequency floor for situational steps (default 0.25)
 *   --stdout               print the file instead of writing it
 *
 * Nothing is fetched: you export the build from the site yourself and point
 * this at the file.
 */
'use strict';

const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const { convert } = require(path.join(ROOT, 'src/main/import-vespene.js'));
const { serializeBuild, parseBuild } = require(path.join(ROOT, 'src/main/parse.js'));

const argv = process.argv.slice(2);
const flag = (name) => argv.includes(`--${name}`);
const value = (name, fallback) => {
  const hit = argv.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : fallback;
};

const source = argv.find((a) => !a.startsWith('--'));
if (!source) {
  console.error('Usage: node tools/import-build.js <export.json> [--list] [--branch=id] [--slot=N]');
  process.exit(1);
}

let data;
try {
  data = JSON.parse(fs.readFileSync(source, 'utf8'));
} catch (err) {
  console.error(`Could not read the JSON: ${err.message}`);
  process.exit(1);
}

// Both directions are spelled out so the choice is never a hidden default.
const keepNotes = flag('notes') && !flag('no-notes');

const result = convert(data, {
  branchId: value('branch'),
  filler: flag('drop-filler') ? 'drop' : 'collapse',
  situational: !flag('no-situational'),
  notes: keepNotes,
  minFrequency: Number(value('min-freq', '0.25')),
});

if (!result.ok) {
  console.error(result.message);
  process.exit(1);
}

if (flag('list')) {
  console.log(`${data.name || '(no name)'}  —  ${result.branches.length} branches\n`);
  for (const b of result.branches) {
    const wr = b.winRate != null ? `${String(Math.round(b.winRate * 100)).padStart(3)}% WR` : '  — WR';
    const games = b.games != null ? `${String(b.games).padStart(4)}g` : '   —g';
    console.log(`  ${b.id === result.selected ? '▶' : ' '} ${b.id.padEnd(10)} ${games}  ${wr}  ${String(b.steps).padStart(4)} steps  ${b.label}`);
  }
  console.log('\nPick one with --branch=<id>.');
  process.exit(0);
}

const slot = value('slot');
if (slot) result.build.slot = Number(slot);

const text = serializeBuild(result.build);

// The converter must not produce something the app cannot read back.
const reparsed = parseBuild(text, 'check.txt');
if (reparsed.problems.length) {
  console.error('The converted result cannot be read back:');
  reparsed.problems.forEach((p) => console.error(`  line ${p.line}: ${p.message}`));
  process.exit(1);
}

if (flag('stdout')) {
  process.stdout.write(text);
} else {
  const base = value('out') || `${(result.build.name || 'imported').split('—')[0].trim().replace(/[\\/:*?"<>|]/g, '').replace(/\s+/g, '-').toLowerCase()}.txt`;
  const filename = /\.(txt|build|md)$/i.test(base) ? base : `${base}.txt`;
  const target = path.join(ROOT, 'builds', filename);
  fs.writeFileSync(target, text, 'utf8');
  console.log(`Saved: builds/${filename}  (${reparsed.steps.length} steps)`);
}

console.error('');
console.error(
  `branch: ${result.selected}  ·  ${reparsed.steps.length} steps  ·  ` +
    `notes ${keepNotes ? 'included (--no-notes to drop)' : 'dropped (--notes to include)'}`
);
result.notes.forEach((n) => console.error(`  ⚠ ${n}`));
if (result.missing.length) {
  console.error('');
  console.error('  To add them to the dictionary, put them in TERMS in src/main/translate.js:');
  result.missing.forEach((k) => console.error(`    ${k}: '',`));
}
