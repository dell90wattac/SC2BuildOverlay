# Notice

## This is an unofficial tool

This program is not associated with, sponsored by, or endorsed by Blizzard
Entertainment.

StarCraft II, the names and images of the units, buildings and upgrades in it,
and the trademarks are all Blizzard Entertainment, Inc.'s.

## Step pictures (icons)

**They are in neither the repository nor the installer.**

The pictures are extracted from the game files and are Blizzard Entertainment's
work. Bundling them would mean copying and distributing that work, so each
person's own PC fetches them directly from the public source when they ask for
them.

- In the app: control window → Overlay → **Download pictures**
- In development: `node tools/fetch-icons.js`

They come from [sc2-planner](https://github.com/BurnySc2/sc2-planner). The code
in that repository is MIT, but the rights to the pictures themselves belong to
Blizzard.

The `assets/icons/manifest.json` in this repository is a table joining terms to
file names, and is this project's own material.

## Pulling build orders out of replays

Reading the replay files is done by **the decoder Blizzard published itself**.

- [s2protocol](https://github.com/Blizzard/s2protocol) — Copyright (c) 2013,
  2017 Blizzard Entertainment. MIT licence.
- [mpyq](https://github.com/arkx/mpyq) — Copyright (c) 2010-2014 Aku
  Kotkavuo. BSD licence. Opens the MPQ archive that holds the replay.

**Neither is included in this program.** Neither is Python. When someone asks to
use the replay feature, their own PC fetches them from [PyPI](https://pypi.org)
and installs them only into a dedicated environment inside the app's data
folder. Deleting that folder leaves no trace, and any Python already on the
system is left untouched.

Python is downloaded and installed by each person from
[python.org](https://www.python.org/downloads/). The app only opens that page in
a browser; it does not download or run the installer.

What gets read is **your own replay file on your own PC**. The game does not
need to be running — there is no contact with the game process.

The unit, building and upgrade names that go into build files are the official
names, mapped through the table in `src/main/translate.js`.

## Font

[Pretendard](https://github.com/orioncactus/pretendard) — SIL Open Font
License 1.1. A copy is at `src/renderer/fonts/OFL.txt`.

## How game data is read

It **only reads** `/game` and `/ui` on `localhost:6119`, which the SC2 client
opens by itself.

- It does not read memory
- It does not inject anything into the game
- It does not write anything to the game

This is the same officially opened channel that broadcast overlays use.

## Code

MIT licence — [LICENSE](LICENSE). That licence applies only to the code this
project wrote itself, not to the game assets above.
