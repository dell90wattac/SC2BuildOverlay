"""SC2 replay -> this app's build-order format.

Every step carries the time production *started*, never the time it finished.
That is what a build order is for: at 2:20 you press the button, and the unit
turns up whenever it turns up.

Where each time comes from:

- Buildings, and units warped in: `SUnitInitEvent` is the moment it was placed
  or the warp began. Already the press.
- Trained units: the tracker only records the birth. The press is in
  `replay.game.events`, but commands there name their ability by a numeric id
  that no decoder ships a table for, so the id is *derived*: an ability used N
  times is followed by N units of one kind after a consistent gap, and the
  shortest of those gaps is the build time. Derived beats tabulated when it
  works, because it sees the Chrono Boost that was actually used.
- Researches: the press is in the commands too, and `research_presses` finds it
  the same way — an ability whose every use falls inside the window where this
  upgrade could have been ordered. Chrono Boost makes this necessary rather than
  merely nicer: it shortens the research but not the table, so subtracting the
  table from the completion lands the step up to a third of the research early.
- Everything neither can pin down — a unit built once, a research whose press is
  ambiguous — falls back to BUILD_TIME / RESEARCH_TIME below.

The report says which of the two produced each number, so a wrong table entry
is visible rather than silently believed.
"""
import argparse
import collections
import glob
import html
import io
import json
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import impshim  # noqa: F401  (installs the `imp` stand-in s2protocol needs)

try:
    from mpyq import MPQArchive
    from s2protocol import versions
except ImportError:
    raise SystemExit('\n'.join([
        '리플레이를 읽는 데 필요한 것이 없습니다. 아래 두 줄을 실행하세요:',
        r'  python -m venv .venv-replay',
        r'  .venv-replay\Scripts\python.exe -m pip install -r tools\replay-requirements.txt',
        r'그 다음부터는 python 이 아니라 .venv-replay\Scripts\python.exe 로 이 스크립트를 실행합니다.',
    ]))

LOOPS = 22.4  # Legacy of the Void, 'Faster'
FOOD_SCALE = 4096

MIN_GAP, MAX_GAP = 4 * LOOPS, 110 * LOOPS
MAX_SPREAD = 15 * LOOPS
MAX_BUILD = 90 * LOOPS
# A derived build time this far from the table is a mis-match, not a Chrono
# Boost. Chrono takes a third off; nothing doubles a build time.
DERIVED_TOLERANCE = 0.55
# Fewer instances than this and any ability used nearby appears to fit.
MIN_TRUSTED = 3

WORKERS = {'Probe', 'SCV', 'Drone'}

NOISE = re.compile(
    r'^(Beacon|LabMineralField|MineralField|RichMineralField|VespeneGeyser|'
    r'RichVespeneGeyser|XelNaga|AdeptPhaseShift|Interceptor|Locust|Broodling|'
    r'Changeling|.*Cocoon|.*Egg|Larva|KD8Charge|AutoTurret|ForceField|'
    r'DisruptorPhased|OracleStasisTrap|CreepTumor|PurificationNova|'
    r'.*Dummy|InfestedTerran|MULE|PointDefenseDrone|GhostAlternate|'
    r'RavenScramblerMissile|RavenRepairDrone)')
NOISE_UPGRADE = re.compile(r'(Reward|Spray|GameHeart|Dance|Emote)')

# A morph turns a building into a different building for good, and that is a
# build-order decision: 궤도 사령부 timing decides a Terran game and a Zerg
# build without 번식지 is not a build. None of it reaches SUnitInitEvent,
# because morphing creates no new unit — only SUnitTypeChangeEvent sees it.
#
# An allowlist, not a denylist: the same event also carries siege mode,
# Viking mode, depot lowering, Liberator mode and every other transient
# state, and those come and go dozens of times per game.
MORPHS = {
    'OrbitalCommand', 'PlanetaryFortress',       # Terran
    'WarpGate',                                  # Protoss
    'Lair', 'Hive', 'GreaterSpire', 'LurkerDen', # Zerg
}

# An add-on's unit type names the building it is attached to, and it goes bare
# while detached: `StarportTechLab` -> `TechLab` -> `BarracksTechLab` is a Tech
# Lab moving from the Starport to the Barracks. That is what a swap actually
# achieves, so that is what gets reported.
ADDON_ON = re.compile(r'^(Barracks|Factory|Starport)?(TechLab|Reactor)$')

# A lift and a landing this far apart are one swap. Beyond it the add-on
# sat free and something else claimed it later, so the landing is its own
# moment rather than a consequence of that lift.
SWAP_WINDOW = 40 * LOOPS

# How close two births have to be to have come from the same keypress. Units
# started together finish together; a queue spaces them a build time apart, and
# the shortest build in the game is nine seconds.
SAME_PRESS = 1.5 * LOOPS

# Chrono Boost only ever targets the caster's own production buildings, and
# it is used more than once in any real game. That is enough to pick it out
# of the numbered abilities without a name table.
MIN_CHRONO = 2

# Chrono Boost is the only thing that speeds a research up, and it caps at +50%.
# Nothing slows one down. Together those bound where the press can be.
CHRONO_MAX = 1.5
# The table is whole seconds where the game's own numbers are not, so a research
# that ran at full speed can still miss its own window by a few loops.
SPAN_SLACK = 1.03
# Identifying the ability is what the window above is for. Once it is known,
# the press outranks the table — a table entry can simply be wrong, and two of
# them were — so all that is left to check is that the press is not absurd,
# which is what catches an ability a patch has renumbered.
TRUST_SPAN = (0.5, 1.5)

# Chrono Boost goes on a Nexus or on something that produces or researches.
# Listing them is what stops an attack order on an own building from being
# mistaken for it.
CHRONO_TARGETS = {
    'Nexus', 'Gateway', 'WarpGate', 'CyberneticsCore', 'Forge',
    'TwilightCouncil', 'RoboticsFacility', 'RoboticsBay', 'Stargate',
    'FleetBeacon', 'TemplarArchives', 'DarkShrine',
}

RACE_CODE = {'프로토스': 'P', '테란': 'T', '저그': 'Z',
             'Protoss': 'P', 'Terran': 'T', 'Zerg': 'Z'}

# Build times, in the same seconds `loops / LOOPS` produces. Only reached when
# the replay itself cannot say — a unit built once, or a research, which happens
# once and so gives the matcher nothing to lock onto.
#
# The unit is the game's own `@time` divided by 1.4, checked against the balance
# data the SC2 editor exports for this build: 수정탑 25 → 17.9, 연결체 100 → 71.4,
# 추적자 38 → 27.1, all matching what the replays measure. An earlier note here
# said these were the wiki's `SC2 Time` column; that column is 18 for a 수정탑,
# and the game says 25, so the two are not the same number and several entries
# had been copied across undivided.
#
# The balance data is Blizzard's, so it is read to check these numbers and never
# vendored: what lives here is the handful of values this tool needs.
BUILD_TIME = {
    # Protoss
    'Probe': 12, 'Zealot': 27, 'Stalker': 27, 'Sentry': 23, 'Adept': 32.5,
    'HighTemplar': 40, 'DarkTemplar': 40, 'Archon': 9, 'Observer': 17.9,
    'WarpPrism': 36, 'Immortal': 39, 'Colossus': 54, 'Disruptor': 36,
    'Phoenix': 25, 'VoidRay': 43, 'Oracle': 37, 'Tempest': 43, 'Carrier': 64,
    'Mothership': 89,
    # Terran
    'SCV': 12, 'Marine': 18, 'Marauder': 21, 'Reaper': 34, 'Ghost': 29,
    'Hellion': 21, 'HellionTank': 21, 'WidowMine': 21, 'SiegeTank': 32,
    'Cyclone': 32, 'Thor': 43, 'VikingFighter': 30, 'Medivac': 30,
    'Liberator': 43, 'Raven': 34, 'Banshee': 43, 'Battlecruiser': 64,
    # Zerg
    'Drone': 12, 'Overlord': 18, 'Queen': 36, 'Zergling': 17, 'Baneling': 14,
    'Roach': 19, 'Ravager': 12.1, 'Hydralisk': 24, 'Lurker': 18, 'Infestor': 35.7,
    'SwarmHostMP': 29, 'Mutalisk': 24, 'Corruptor': 29, 'BroodLord': 24,
    'Viper': 29, 'Ultralisk': 39, 'Overseer': 12,
}

# Research times, same unit as BUILD_TIME, keyed the way normalize() files them
# (race prefix and `Level` stripped) so one entry covers every spelling a replay
# might use. Unlike BUILD_TIME these cannot be derived from a replay — a
# research happens once — which is why a step built on one of these is reported
# rather than just trusted.
#
# One key does not always serve three races: 프로토스 지상 무기 is 121.4 where
# 테란 보병 무기 and 저그 근접 공격 are 114.3. That works out because each race
# normalises to its own key first (저그 갑피 to GroundCarapace, 테란 보병 to
# InfantryWeapons), leaving the bare GroundWeapons/GroundArmor entries serving
# Protoss alone. Check that before editing one of those numbers.
RESEARCH_TIME = {
    # Protoss
    'WarpGate': 100, 'Charge': 100, 'Blink': 121, 'ResonatingGlaives': 100,
    'PsiStorm': 79, 'GraviticBoosters': 57, 'GraviticDrive': 57,
    'ExtendedThermalLance': 100, 'ShadowStride': 100,
    'AnionPulseCrystals': 64, 'FluxVanes': 57, 'TectonicDestabilizers': 100,
    'GroundWeapons1': 121.4, 'GroundWeapons2': 144.6, 'GroundWeapons3': 167.9,
    'GroundArmor1': 121.4, 'GroundArmor2': 144.6, 'GroundArmor3': 167.9,
    'ShieldsLevel1': 121.4, 'ShieldsLevel2': 144.6, 'ShieldsLevel3': 167.9,
    'AirWeapons1': 129, 'AirWeapons2': 154, 'AirWeapons3': 179,
    'AirArmor1': 129, 'AirArmor2': 154, 'AirArmor3': 179,
    # Terran
    'Stimpack': 100, 'CombatShield': 79, 'ConcussiveShells': 43,
    'InfernalPreigniter': 79, 'DrillingClaws': 79, 'SmartServos': 79,
    'MagFieldAccelerator': 100, 'InterferenceMatrix': 57,
    'CloakingField': 79, 'BansheeSpeed': 79,
    'AdvancedBallistics': 79, 'YamatoCannon': 100, 'PersonalCloaking': 85.7,
    'CaduceusReactor': 50,
    'HiSecAutoTracking': 57, 'BuildingArmor': 100, 'NeosteelArmor': 100,
    'InfantryWeapons1': 114, 'InfantryWeapons2': 136, 'InfantryWeapons3': 157,
    'InfantryArmor1': 114, 'InfantryArmor2': 136, 'InfantryArmor3': 157,
    'VehicleWeapons1': 114, 'VehicleWeapons2': 136, 'VehicleWeapons3': 157,
    'ShipWeapons1': 114, 'ShipWeapons2': 136, 'ShipWeapons3': 157,
    # The dictionary files this one under both names, so both need a time.
    'VehicleAndShipArmor1': 114, 'VehicleAndShipArmor2': 136,
    'VehicleAndShipArmor3': 157,
    'VehicleAndShipPlating1': 114, 'VehicleAndShipPlating2': 136,
    'VehicleAndShipPlating3': 157,
    # Zerg
    'MetabolicBoost': 79, 'AdrenalGlands': 93, 'CentrifugalHooks': 71.4,
    'GlialReconstitution': 79, 'TunnelingClaws': 79,
    'GroovedSpines': 50, 'MuscularAugments': 64,
    # `Frenzy` in the replay: 1440 loops in every one that has it.
    'Frenzy': 64,
    'AdaptiveTalons': 57, 'SeismicSpines': 57,
    'PneumatizedCarapace': 43, 'Burrow': 71,
    'ChitinousPlating': 79, 'AnabolicSynthesis': 42.9,
    'NeuralParasite': 79,
    'MeleeAttacks1': 114, 'MeleeAttacks2': 136, 'MeleeAttacks3': 157,
    'MissileAttacks1': 114, 'MissileAttacks2': 136, 'MissileAttacks3': 157,
    'GroundCarapace1': 114, 'GroundCarapace2': 136, 'GroundCarapace3': 157,
    'FlyerAttacks1': 114, 'FlyerAttacks2': 136, 'FlyerAttacks3': 157,
    'FlyerCarapace1': 114, 'FlyerCarapace2': 136, 'FlyerCarapace3': 157,
}

ALIASES = {
    # The replay names the research after the unit it helps; the dictionary and
    # the icon pack both file it under the research's own name.
    'MedivacCaduceusReactor': 'CaduceusReactor',
    # Only names no rule can reach: an internal name that looks nothing like
    # the displayed one. Anything differing by a race prefix, a `Level` before
    # the number, or a building-plus-add-on pairing is handled by normalize().
    # Protoss
    'BlinkTech': 'Blink', 'PsiStormTech': 'PsiStorm',
    'ObserverGraviticBooster': 'GraviticBoosters',
    'DarkTemplarBlinkUpgrade': 'ShadowStride',
    'PhoenixRangeUpgrade': 'AnionPulseCrystals',
    'VoidRaySpeedUpgrade': 'FluxVanes',
    'TempestGroundAttackUpgrade': 'TectonicDestabilizers',
    'AdeptPiercingAttack': 'ResonatingGlaives',
    'WarpGateResearch': 'WarpGate',
    # Terran
    'PunisherGrenades': 'ConcussiveShells',
    'CycloneLockOnDamageUpgrade': 'MagFieldAccelerator',
    'ShieldWall': 'CombatShield',
    'BansheeCloak': 'CloakingField',
    'HighCapacityBarrels': 'InfernalPreigniter',
    'LiberatorAGRangeUpgrade': 'AdvancedBallistics',
    'BattlecruiserEnableSpecializations': 'YamatoCannon',
    'VikingFighter': 'Viking', 'SwarmHostMP': 'SwarmHost',
    # Zerg
    'ZerglingMovementSpeed': 'MetabolicBoost',
    'ZerglingAttackSpeed': 'AdrenalGlands',
    'CentrificalHooks': 'CentrifugalHooks',
    'DrillClaws': 'DrillingClaws',
    'EvolveGroovedSpines': 'GroovedSpines',
    'EvolveMuscularAugments': 'MuscularAugments',
    'overlordspeed': 'PneumatizedCarapace',
    'anabolicsynthesis': 'AnabolicSynthesis',
    'DiggingClaws': 'AdaptiveTalons',
    'LurkerRange': 'SeismicSpines',
    'InfestorEnergyUpgrade': 'PathogenGlands',
}


# Which numbered ability orders which research, as read off replays by
# research_abilities() below. It is game data rather than anything this tool
# decides, so it holds from one replay to the next and is worth keeping: one
# replay on its own often cannot name more than a couple of these, and without
# them a Protoss research falls back to the table and lands early.
#
# Read from 5.0.16 (base build 97563) over 217 replays. A patch may renumber
# an ability, so
# nothing here is trusted on sight: a seeded id is used only where this run
# could not work the answer out for itself, and only when its press still falls
# inside the window the research could have been ordered in. A renumbered one
# simply stops matching and the table takes over again.
RESEARCH_ABILITY = {
    # Fusion Core
    'MedivacCaduceusReactor': (237, 3),
    # Roach Warren
    'GlialReconstitution': (109, 1),
    # Engineering Bay
    'HiSecAutoTracking': (164, 0), 'TerranInfantryWeaponsLevel1': (164, 2),
    'TerranInfantryArmorsLevel1': (164, 6),
    # Barracks Tech Lab
    'Stimpack': (167, 0), 'ShieldWall': (167, 1), 'PunisherGrenades': (167, 2),
    # Factory Tech Lab
    'HighCapacityBarrels': (168, 1),
    # Armory
    'TerranVehicleWeaponsLevel1': (171, 5),
    'TerranVehicleAndShipArmorsLevel1': (171, 14),
    # Forge
    'ProtossGroundWeaponsLevel1': (182, 0),
    'ProtossGroundWeaponsLevel2': (182, 1),
    'ProtossGroundArmorsLevel1': (182, 3),
    'ProtossGroundArmorsLevel2': (182, 4), 'ProtossShieldsLevel1': (182, 6),
    # Robotics Bay
    'ExtendedThermalLance': (183, 5),
    # Templar Archives
    'PsiStormTech': (184, 4),
    # Evolution Chamber
    'ZergMeleeWeaponsLevel1': (187, 0), 'ZergMeleeWeaponsLevel2': (187, 1),
    'ZergGroundArmorsLevel1': (187, 3), 'ZergGroundArmorsLevel2': (187, 4),
    'ZergMissileWeaponsLevel1': (187, 6),
    # Hatchery
    'overlordspeed': (191, 1), 'Burrow': (191, 3),
    # Spawning Pool
    'zerglingattackspeed': (192, 0), 'zerglingmovementspeed': (192, 1),
    # Hydralisk Den
    'EvolveGroovedSpines': (193, 0), 'EvolveMuscularAugments': (193, 1),
    'Frenzy': (193, 2),
    # Spire
    'ZergFlyerWeaponsLevel1': (194, 0),
    # Baneling Nest
    'CentrificalHooks': (226, 0),
    # Cybernetics Core
    'ProtossAirWeaponsLevel1': (238, 0), 'ProtossAirArmorsLevel1': (238, 3),
    'WarpGateResearch': (238, 6),
    # Twilight Council
    'Charge': (239, 0), 'BlinkTech': (239, 1), 'AdeptPiercingAttack': (239, 2),
    # Lurker Den
    'LurkerRange': (715, 1),
}


RESEARCH_STRUCTURE = {
    109: 'RoachWarren', 164: 'EngineeringBay', 167: 'BarracksTechLab',
    168: 'FactoryTechLab', 171: 'Armory', 182: 'Forge', 183: 'RoboticsBay',
    184: 'TemplarArchive', 187: 'EvolutionChamber', 191: 'Hatchery',
    192: 'SpawningPool', 193: 'HydraliskDen', 194: 'Spire',
    226: 'BanelingNest', 238: 'CyberneticsCore', 239: 'TwilightCouncil',
    237: 'FusionCore', 715: 'LurkerDenMP',
}

# Which ability trains which unit, read off 217 replays the same way
# RESEARCH_ABILITY was: an ability that pairs with a unit's births game after
# game, one ability to one unit. The ids group themselves by the building that
# owns them, which is the check that they are right — 161 turns out to be the
# Barracks, 174 the Gateway, 176 the Robotics Facility, and no id explains two
# buildings' worth of units.
#
# All 217 were 5.0.16 (base build 97563), so this is confirmed for one build and
# says nothing about whether a patch keeps the numbering — m_abilLink is an
# index into that build's ability table, not a stable id. Unlike a research,
# which is re-checked against the window it could have been ordered in, a train
# press has only its gap to a birth to vouch for it, and a renumbered ability
# would still hand back gaps in a plausible range. So it is checked against the
# one thing that cannot be coincidence: nothing is trained from a building the
# player never finished.
#
# Workers are absent on purpose: they collapse to one `계속 생산` line, so their
# timing is not a step anyone follows.
#
# Zerg was absent too, on the reasoning that a larva morph leaves no press to
# pair. It does — link 195 is the larva and 245 the hatchery — but the pairing
# gave up before it could see them: it required at least as many commands as
# births, and selecting five larvae and pressing once makes ten 저글링 off one
# command. Read the same way as the rest once that bar was lifted, and the
# numbers agree with the table to within two per cent across 447 저글링.
TRAIN_ABILITY = {
    # Barracks
    'Marine': (161, 0), 'Reaper': (161, 1), 'Marauder': (161, 3),
    # Factory
    'SiegeTank': (162, 1), 'Thor': (162, 4), 'Hellion': (162, 5),
    'Cyclone': (162, 7), 'WidowMine': (162, 24),
    # Starport
    'Medivac': (163, 0), 'Banshee': (163, 1), 'Raven': (163, 2),
    'Battlecruiser': (163, 3),
    'VikingFighter': (163, 4), 'Liberator': (163, 6),
    # Gateway
    'Zealot': (174, 0), 'Stalker': (174, 1), 'Sentry': (174, 5),
    'Adept': (174, 6),
    # Stargate
    'Phoenix': (175, 0), 'VoidRay': (175, 4), 'Oracle': (175, 8),
    'Tempest': (175, 9),
    # Robotics Facility
    'WarpPrism': (176, 0), 'Observer': (176, 1), 'Colossus': (176, 2),
    'Immortal': (176, 3),
    # Larva
    'Zergling': (195, 1), 'Overlord': (195, 2), 'Hydralisk': (195, 3),
    'Mutalisk': (195, 4), 'Ultralisk': (195, 6), 'Roach': (195, 9),
    'Corruptor': (195, 11),
    # Hatchery
    'Queen': (245, 0),
}

# What each of those ability ids belongs to. Nothing can be ordered from a
# building before the building is finished, which is the floor every fallback
# gets held to — a table time subtracted off a Chrono Boosted unit otherwise
# reports it a few seconds before the building that made it existed.
PRODUCER = {
    161: 'Barracks', 162: 'Factory', 163: 'Starport',
    174: 'Gateway', 175: 'Stargate', 176: 'RoboticsFacility',
    195: 'Hatchery', 245: 'Hatchery',
}

# Every player starts with one of these already standing, and a starting
# building is never finished — it has no init and no done event — so it never
# reaches the finished list. Owning one proves nothing and missing one proves
# nothing, which makes it useless both as the check that an ability id is still
# right and as a floor: a 저글링 pressed at 0:30 would otherwise be dragged
# forward to whenever the second 부화장 went up.
STARTING = {'Hatchery', 'Nexus', 'CommandCenter'}

# Units whose ability number is not in the table above, so the press cannot be
# read and the step falls back to subtracting a build time. Naming the building
# they come out of at least holds that subtraction to the moment the building
# existed: Chrono Boost shortens the real build but not the table, so without a
# floor a boosted 모선 lands thirty seconds before the 연결체 that made it.
#
# 집정관 is a merge rather than a unit that is trained, but both templars come
# out of a Gateway, so that is still the earliest it can have happened.
TRAINED_AT = {
    'Mothership': 'Nexus',
    'Carrier': 'Stargate',
    'HighTemplar': 'Gateway',
    'DarkTemplar': 'Gateway',
    'Disruptor': 'RoboticsFacility',
    'Archon': 'Gateway',
}


def producer_of(name):
    """The building a unit is made in — by ability id where that is known."""
    key = TRAIN_ABILITY.get(name)
    if key:
        return PRODUCER.get(key[0])
    return TRAINED_AT.get(name)


# The tracker spells some upgrades entirely in lower case —
# `zerglingmovementspeed` beside `ZerglingAttackSpeed` in the same replay — and
# which ones it does that to is not a list worth keeping twice.
ALIASES_CI = {key.lower(): value for key, value in ALIASES.items()}


def alias_of(name):
    """The alias for a name whatever its case, or the name unchanged."""
    return ALIASES.get(name) or ALIASES_CI.get(name.lower()) or name


def txt(value):
    return value.decode('utf8', 'replace') if isinstance(value, bytes) else value


CLAN_TAG = re.compile(r'<[^>]*>')


def player_name(raw):
    """The name without the clan-tag markup the replay stores it with.

    A name in a clan arrives as '&lt;TAG&gt;<sp/>Name': the clan tag, escaped,
    then a marker standing in for the space, then the name itself.
    """
    return CLAN_TAG.sub(' ', html.unescape(txt(raw))).strip()


def clock(loop):
    total = int(round(loop / LOOPS))
    return '%d:%02d' % (total // 60, total % 60)


def write_lines(path, lines):
    """One place, so the line ending is right once rather than three times.

    Text mode already turns \\n into the platform ending; joining with
    os.linesep on top of that wrote every line as \\r\\r\\n.
    """
    with io.open(path, 'w', encoding='utf-8') as out:
        out.write('\n'.join(lines) + '\n')


def expand(paths):
    """Turns whatever was typed into a list of replay files.

    A folder, or a wildcard, or plain file names. PowerShell hands wildcards
    through unexpanded — unlike a POSIX shell — so the script has to do it, or
    `replay/*.SC2Replay` reaches us as a literal filename and fails to open.
    """
    out = []
    for item in paths:
        if os.path.isdir(item):
            out += sorted(glob.glob(os.path.join(item, '*.SC2Replay')))
        elif any(c in item for c in '*?['):
            out += sorted(glob.glob(item))
        else:
            out.append(item)
    if not out:
        raise SystemExit('리플레이 파일을 찾지 못했습니다: %s' % ' '.join(paths))
    return out


def load_terms(repo):
    path = os.path.join(repo, 'src', 'main', 'translate.js')
    text = io.open(path, encoding='utf-8').read()
    start = text.index('const TERMS = {')
    return dict(re.findall(r"^\s{2}(\w+):\s*'([^']+)'",
                           text[start:text.index('\n};', start)], re.M))


def load_buildings(repo):
    """The Korean terms that name a building.

    Only needed to count them: buildings go 2개, units go 2기. The icon
    manifest already sorts the two — its asset names are btn-building-* and
    btn-unit-* — so there is no second list here to fall out of step with it.
    """
    path = os.path.join(repo, 'assets', 'icons', 'manifest.json')
    try:
        terms = json.load(io.open(path, encoding='utf-8'))['terms']
    except (IOError, OSError, ValueError, KeyError):
        return set()
    return {korean for korean, asset in terms.items() if '-building-' in asset}


def decoder_for(base_build):
    """The decoder for this game build, or the newest one we have.

    s2protocol ships one generated decoder per game build, so a replay from a
    patch newer than the installed s2protocol has none and raises ImportError.
    Falling back to the newest is nearly always right — the event streams this
    reads change rarely, and far less often than the build number does — and it
    beats refusing to open the replay at all. The fallback is reported, so a
    build that came out wrong has somewhere to point.

    @returns (module, the build actually used, or None when it was an exact match)
    """
    try:
        return versions.build(base_build), None
    except (ImportError, KeyError, AttributeError):
        newest = versions.latest()
        used = newest.__name__.rsplit('.', 1)[-1].replace('protocol', '')
        return newest, used or 'latest'


class Replay(object):
    def __init__(self, path):
        self.path = path
        archive = MPQArchive(path)
        self.header = versions.latest().decode_replay_header(
            archive.header['user_data_header']['content'])
        proto, self.fell_back_to = decoder_for(self.header['m_version']['m_baseBuild'])
        self.details = proto.decode_replay_details(archive.read_file('replay.details'))
        self.tracker = list(proto.decode_replay_tracker_events(
            archive.read_file('replay.tracker.events')))
        self.game = list(proto.decode_replay_game_events(
            archive.read_file('replay.game.events')))

    @property
    def version(self):
        v = self.header['m_version']
        return '%d.%d.%d.%d' % (v['m_major'], v['m_minor'], v['m_revision'], v['m_build'])

    @property
    def seconds(self):
        return self.header['m_elapsedGameLoops'] / LOOPS

    def players(self):
        """One row per player, with the ids needed to read their events.

        `SPlayerSetupEvent` is what ties a tracker player to the user whose
        commands appear in the game events; guessing that link from the order of
        the lists happens to work in a game against the AI and breaks the moment
        both sides are human.
        """
        users = {}
        for event in self.tracker:
            if event['_event'].endswith('SPlayerSetupEvent'):
                users[event['m_playerId']] = event.get('m_userId')
        out = []
        for index, player in enumerate(self.details['m_playerList'], start=1):
            out.append({
                'id': index,
                'user': users.get(index),
                'name': player_name(player['m_name']),
                'race': txt(player['m_race']),
                'human': player.get('m_control') == 2,
                'won': player['m_result'] == 1,
            })
        return out


def train_commands(replay, user):
    out = {}
    if user is None:
        return out
    for event in replay.game:
        if not event['_event'].endswith('SCmdEvent'):
            continue
        if (event.get('_userid') or {}).get('m_userId') != user:
            continue
        ability = event.get('m_abil')
        if not ability or ability.get('m_abilLink') is None:
            continue
        out.setdefault((ability['m_abilLink'], ability.get('m_abilCmdIndex')),
                       []).append(event['_gameloop'])
    for loops in out.values():
        loops.sort()
    return out


def pair(cmd_loops, born_loops):
    """Match births to commands the way a production queue does: first in, first
    out. Pairing a birth with the *nearest* preceding command instead makes the
    gaps meaningless, since that is whatever was ordered last.

    @returns (spread, build_time, count) or None.
    """
    if len(cmd_loops) < len(born_loops) or not born_loops:
        return None
    gaps = []
    at = 0
    for born in sorted(born_loops):
        while at < len(cmd_loops) and (cmd_loops[at] >= born
                                       or born - cmd_loops[at] > MAX_GAP):
            at += 1
        if at >= len(cmd_loops):
            return None
        gap = born - cmd_loops[at]
        if gap < MIN_GAP:
            return None
        gaps.append(gap)
        at += 1
    spread = statistics.pstdev(gaps) if len(gaps) > 1 else 0.0
    if spread > MAX_SPREAD:
        return None
    # The shortest gap waited for nothing ahead of it: that is the build time.
    return spread, min(gaps), len(gaps)


def unit_tags(replay):
    """Every unit tag the tracker mentions, and what it currently is.

    Commands name their target by tag, and morphs are reported by tag, so this
    is what turns either into a unit name. Type changes are applied in order,
    so the map holds whatever the unit was last seen as.
    """
    owner = {}
    kind_of = {}
    for event in replay.tracker:
        kind = event['_event'].rsplit('.', 1)[-1]
        tag = event.get('m_unitTagIndex')
        if tag is None:
            continue
        if kind in ('SUnitBornEvent', 'SUnitInitEvent'):
            owner[tag] = event.get('m_controlPlayerId')
            kind_of[tag] = txt(event['m_unitTypeName'])
        elif kind == 'SUnitTypeChangeEvent':
            kind_of[tag] = txt(event['m_unitTypeName'])
    return owner, kind_of


def morph_steps(replay, player, owner):
    """Buildings that became something else for good.

    SUnitTypeChangeEvent says which unit changed but not who owns it, so
    ownership comes from the tag map built off the birth and placement events.
    """
    out = []
    seen = set()
    for event in replay.tracker:
        if not event['_event'].endswith('SUnitTypeChangeEvent'):
            continue
        tag = event['m_unitTagIndex']
        if owner.get(tag) != player['id']:
            continue
        name = txt(event['m_unitTypeName'])
        if name not in MORPHS:
            continue
        # Only the first time. A building that lifts off and lands again
        # re-announces its own type, so an Orbital Command that gets moved
        # reports the morph once per landing — three times in one game here.
        if (tag, name) in seen:
            continue
        seen.add((tag, name))
        out.append({'loop': event['_gameloop'], 'name': name,
                    'kind': 'morph', 'source': 'event'})
    return out


def swap_steps(replay, player, owner):
    """Add-ons that changed hands, reported as what they ended up on.

    Followed on the add-on rather than the building. Two buildings lifting off
    says a swap happened but not what came of it, and the useful line is the
    outcome — 병영 기술실 — not the fact that something took off.

    Detached is the bare type; attached names the building. So a bare state
    followed by a named one is the moment the add-on landed on something new.
    An Init event resets the history, because tag numbers get reused (one tag
    here was a Viking before it was a Tech Lab).
    """
    attached = {}
    hosted = {}     # the building it was on before it came free
    out = []
    for event in replay.tracker:
        kind = event['_event'].rsplit('.', 1)[-1]
        tag = event.get('m_unitTagIndex')
        if tag is None or kind not in ('SUnitInitEvent', 'SUnitBornEvent',
                                       'SUnitTypeChangeEvent'):
            continue
        addon = ADDON_ON.match(txt(event['m_unitTypeName']))
        if not addon:
            attached.pop(tag, None)
            continue
        building, part = addon.group(1), addon.group(2)

        if kind != 'SUnitTypeChangeEvent':
            attached[tag] = building   # freshly built, or a reused tag
            continue

        was = attached.get(tag, 'unknown')
        attached[tag] = building
        if building is None:
            # Came off. Named for the building it was on, which is the one
            # that just gave up its add-on.
            if was and was != 'unknown' and owner.get(tag) == player['id']:
                hosted[tag] = was
                # Named for the add-on alone, with the building it left in the
                # note. Calling it `우주공항 기술실 분리` and then
                # `병영 기술실` two lines later names one Tech Lab two ways and
                # reads as two of them; and a bare add-on icon is itself what
                # detached looks like.
                out.append({'loop': event['_gameloop'], 'name': part,
                            'kind': 'detach', 'source': 'event',
                            'from': was})
            continue
        # Only a move onto a building counts; going bare was handled above.
        if was is not None or owner.get(tag) != player['id']:
            continue
        out.append({'loop': event['_gameloop'], 'name': building + part,
                    'kind': 'swap', 'source': 'event',
                    # Where it came from. Naming the partner is what makes the
                    # trade readable — `병영 기술실 // 우주공항에서` says the whole
                    # swap on one line, without a second line for the detach
                    # that the result already implies.
                    'from': hosted.pop(tag, None)})
    return out


def mule_steps(replay, player):
    """Every MULE call-down. A MULE appears at once, so its birth is the press."""
    out = []
    for event in replay.tracker:
        if not event['_event'].endswith('SUnitBornEvent'):
            continue
        if event.get('m_controlPlayerId') != player['id']:
            continue
        if txt(event['m_unitTypeName']) != 'MULE':
            continue
        out.append({'loop': event['_gameloop'], 'name': 'MULE',
                    'kind': 'mule', 'source': 'event'})
    return out


def chrono_steps(replay, player):
    """Chrono Boost casts, and the building each one went on.

    The ability is numbered, not named, so it is identified by what it does:
    every use targets a building the caster owns, and it is used more than
    once. Attack orders fail the first test (they land on units, and on the
    opponent's), which is what separates them.
    """
    if RACE_CODE.get(player['race']) != 'P' or player['user'] is None:
        return []

    owner, kind_of = unit_tags(replay)
    by_ability = collections.defaultdict(list)
    for event in replay.game:
        if not event['_event'].endswith('SCmdEvent'):
            continue
        if (event.get('_userid') or {}).get('m_userId') != player['user']:
            continue
        ability = event.get('m_abil') or {}
        if ability.get('m_abilLink') is None:
            continue
        target = (event.get('m_data') or {}).get('TargetUnit')
        if not target or not target.get('m_tag'):
            continue
        # The tracker's tag index sits in the high bits of a command's tag.
        index = target['m_tag'] >> 18
        if owner.get(index) != player['id']:
            continue
        by_ability[(ability['m_abilLink'], ability.get('m_abilCmdIndex'))].append(
            (event['_gameloop'], kind_of.get(index)))

    best = None
    for key, rows in by_ability.items():
        if len(rows) < MIN_CHRONO:
            continue
        # Every target must be a building this player owns. A command that ever
        # targeted a unit is not Chrono Boost.
        if any(name is None or name not in CHRONO_TARGETS for _, name in rows):
            continue
        if best is None or len(rows) > len(best[1]):
            best = (key, rows)

    if not best:
        return []
    return [{'loop': loop, 'name': 'ChronoBoost', 'kind': 'chrono',
             'source': 'event', 'on': name} for loop, name in sorted(best[1])]


def births(replay, player):
    """Units this player produced, by kind.

    A birth names the ability that made it, which is how a hallucination is
    told from the real thing. A 파수기 scouting with a hallucinated 예언자 is
    ordinary Protoss play and the corpus has ninety-nine of them; every one was
    turning up in a build order as a 예언자 nobody built, dragging a step into
    the list that cannot be followed and that vanishes again in forty seconds.
    """
    out = {}
    for event in replay.tracker:
        if not event['_event'].endswith('SUnitBornEvent'):
            continue
        if event.get('m_controlPlayerId') != player['id'] or event['_gameloop'] == 0:
            continue
        made_by = event.get('m_creatorAbilityName')
        if made_by and txt(made_by).startswith('Hallucination'):
            continue
        name = txt(event['m_unitTypeName'])
        if not NOISE.match(name):
            out.setdefault(name, []).append(event['_gameloop'])
    return out


def derive_build_times(replays_and_players):
    """Build times read off the replays, one ability to one unit.

    Decided over every replay at once. Inside a single replay the ability that
    trains Probes has enough commands to also 'explain' the Zealots, and the
    leftover gaps pass for a 70-second build time — which put Stalkers on the
    timeline ahead of the Cybernetics Core that allows them.
    """
    pooled = collections.defaultdict(list)
    for replay, player in replays_and_players:
        commands = train_commands(replay, player['user'])
        for name, loops in births(replay, player).items():
            for key, cmd_loops in commands.items():
                got = pair(cmd_loops, loops)
                if got and got[1] <= MAX_BUILD:
                    pooled[(name, key)].append(got)

    ranked = []
    for (name, key), rows in pooled.items():
        builds = [r[1] for r in rows]
        ranked.append({
            'unit': name, 'ability': key, 'replays': len(rows),
            'spread': statistics.mean(r[0] for r in rows),
            'build': statistics.median(builds),
            'drift': (max(builds) - min(builds)) if len(builds) > 1 else 0.0,
            'count': sum(r[2] for r in rows),
        })
    ranked.sort(key=lambda r: (-r['replays'], r['drift'], r['spread']))

    chosen = {}
    used = set()
    for row in ranked:
        if row['unit'] in chosen or row['ability'] in used:
            continue
        if row['count'] < MIN_TRUSTED:
            continue
        table = BUILD_TIME.get(row['unit'])
        if table:
            ratio = (row['build'] / LOOPS) / table
            # Chrono Boost shortens; nothing lengthens a build time, and a
            # doubled one means the ability was not the right one.
            if not (DERIVED_TOLERANCE <= ratio <= 1.25):
                continue
        chosen[row['unit']] = row
        used.add(row['ability'])
    return chosen


def build_time(name, derived):
    """(loops, source) — the replay's own number when it earned trust."""
    if name in derived:
        return derived[name]['build'], 'derived'
    table = in_table(BUILD_TIME, name)
    if table:
        return table * LOOPS, 'table'
    return 0, 'unknown'


def research_time(name):
    table = in_table(RESEARCH_TIME, name)
    return (table * LOOPS, 'table') if table else (0, 'unknown')


def supply_curve(replay, player):
    out = []
    for event in replay.tracker:
        if event['_event'].endswith('SPlayerStatsEvent') and event['m_playerId'] == player['id']:
            used = (event.get('m_stats') or {}).get('m_scoreValueFoodUsed')
            if used is not None:
                out.append((event['_gameloop'], used // FOOD_SCALE))
    return out


def supply_at(curve, loop):
    """Supply at that moment, from the periodic stats samples.

    A step can land before the first sample — a build time subtracted off an
    early unit clamps to 0:00 — and there the first sample is the answer, since
    nothing has been built yet and supply is still what the game started with.
    Returning nothing instead dropped the `@N` from exactly the first step.
    """
    if not curve:
        return None
    found = curve[0][1]
    for at, value in curve:
        if at > loop:
            break
        found = value
    return found


def collapse(steps):
    """One line per decision. Workers run all game, so they become one line."""
    out = []
    seen = set()
    merged = set()
    index = 0
    while index < len(steps):
        if index in merged:
            index += 1
            continue
        step = steps[index]
        if step['name'] in WORKERS and step['kind'] == 'unit':
            if step['name'] not in seen:
                seen.add(step['name'])
                out.append(dict(step, count=1, filler=True))
            index += 1
            continue
        # Same kind as well as same name. Two Tech Labs built together are one
        # line, but one built and one taken in a swap are two different
        # decisions — merging them swallowed the swap and its note.
        #
        # Scanned past anything that happens in between rather than stopping at
        # it: two 광전사 a second apart with an 관측선 landing between them are
        # still one `광전사 2기`, and before this they broke into two lines
        # whenever something else shared the moment.
        count = 0
        ahead = index
        while (ahead < len(steps)
               and steps[ahead]['loop'] - step['loop'] <= 12 * LOOPS):
            if (steps[ahead]['name'] == step['name']
                    and steps[ahead]['kind'] == step['kind']):
                count += 1
                merged.add(ahead)
            ahead += 1
        out.append(dict(step, count=count, filler=False))
        index += 1
    return out


RACE_PREFIX = re.compile(r'^(Terran|Zerg|Protoss)(?=[A-Z])')
# `LurkerDenMP`, `SwarmHostMP`, `LurkerMP`: a multiplayer suffix the replay
# carries and the dictionary never does.
MP_SUFFIX = re.compile(r'MP$')
LEVEL_SUFFIX = re.compile(r'Level(\d)$')
ADDON = re.compile(r'^(\w+?)(TechLab|Reactor)$')


def normalize(name):
    """The spellings a replay's name might be filed under, best first.

    Replays and the rest of the app spell the same thing differently often
    enough that listing every pair by hand guarantees gaps — the first Terran
    replay turned up eleven missing names and nine missing times. So the
    differences that follow a rule are rules, and ALIASES holds only the
    genuinely irregular ones.

    The rules: an alias; a race prefix (`TerranInfantryWeaponsLevel1`); a
    `Level` before the number (`...WeaponsLevel1` vs `...Weapons1`); and those
    combined. Yielded rather than resolved, so one walk serves the Korean
    dictionary and both time tables.
    """
    # A function, not a backreference: a replacement string of one backslash
    # and a 1 is a transcription slip away from the control character it
    # resembles, and it silently produced 'GroundWeapons\x01' once already.
    def drop_level(word):
        return LEVEL_SUFFIX.sub(lambda m: m.group(1), word)

    stem = alias_of(name)
    plain = RACE_PREFIX.sub('', stem)
    flat = drop_level(plain)

    # The multiplayer suffix, and the alias that may be hiding behind it.
    bare = MP_SUFFIX.sub('', flat) if MP_SUFFIX.search(flat) else None

    # `TemplarArchive` in the replay, `TemplarArchives` in the dictionary.
    # Which of the pair is the plural one is not worth a rule in each
    # direction, so both spellings are offered and the tables pick.
    other = None
    if flat[-1:].isalpha():
        other = flat[:-1] if flat.endswith('s') else flat + 's'

    # 'ProtossGroundArmorsLevel1' -> 'GroundArmor1'. The replay pluralises the
    # thing being upgraded, the dictionary does not, and the difference sits
    # behind the number rather than at the end where it could just be stripped.
    singular = None
    if flat[-1:].isdigit() and flat[-2:-1] == 's':
        singular = flat[:-2] + flat[-1]

    # Zerg names the same upgrades differently: armour is 갑피 (carapace) and
    # attack is 공격 (attacks), where the other two races say 장갑 and 무기. Both
    # `ProtossGroundArmorsLevel1` and `ZergGroundArmorsLevel1` strip to the same
    # word, so the race cannot be dropped before this is applied — and Zerg is
    # the race that renames, so Zerg carries the rule.
    zergish = None
    if name.startswith('Zerg') and flat[-1:].isdigit():
        head, level = flat[:-1], flat[-1]
        for was, becomes in (('Armors', 'Carapace'), ('Armor', 'Carapace'),
                             ('Weapons', 'Attacks'), ('Weapon', 'Attacks')):
            if head.endswith(was):
                zergish = head[:-len(was)] + becomes + level
                break

    # Same shape, but where the singular is itself irregular.
    numbered = None
    if flat[-1:].isdigit() and flat[:-1] in ALIASES:
        numbered = ALIASES[flat[:-1]] + flat[-1:]

    seen = set()
    for candidate in (name, stem, zergish, plain, flat, singular, numbered,
                      bare, alias_of(bare) if bare else None, other,
                      ALIASES.get(plain), ALIASES.get(flat),
                      drop_level(ALIASES.get(plain, plain))):
        if candidate and candidate not in seen:
            seen.add(candidate)
            yield candidate



def in_table(table, name):
    """The table's value for a replay's name, under any of its spellings."""
    for candidate in normalize(name):
        if candidate in table:
            return table[candidate]
    return None


def korean_for(name, terms):
    """The dictionary's word for a replay's name, or None."""
    found = in_table(terms, name)
    if found:
        return found

    # `BarracksTechLab` -> 병영 + 기술실. The dictionary keeps the building and
    # the add-on apart, because that is how the two are named in game.
    addon = ADDON.match(ALIASES.get(name, name))
    if addon and addon.group(1) in terms and addon.group(2) in terms:
        return '%s %s' % (terms[addon.group(1)], terms[addon.group(2)])
    return None


def command_presses(replay, player):
    """Every untargeted ability press, by ability.

    A research is cast on a structure that is already selected, so it carries
    no target of its own. Move, attack and building placement all carry one,
    which is most of what a game's commands are.
    """
    out = collections.defaultdict(list)
    if player['user'] is None:
        return out
    for event in replay.game:
        if not event['_event'].endswith('SCmdEvent'):
            continue
        if (event.get('_userid') or {}).get('m_userId') != player['user']:
            continue
        ability = event.get('m_abil') or {}
        if ability.get('m_abilLink') is None:
            continue
        data = event.get('m_data') or {}
        if data.get('TargetUnit') or data.get('TargetPoint'):
            continue
        out[(ability['m_abilLink'], ability.get('m_abilCmdIndex'))].append(
            event['_gameloop'])
    return out


def upgrades_of(replay, player):
    """(name, the loop it finished on, how long it takes) per research done."""
    out = []
    for event in replay.tracker:
        if not event['_event'].endswith('SUpgradeEvent'):
            continue
        if event['m_playerId'] != player['id'] or event['_gameloop'] <= 0:
            continue
        name = txt(event['m_upgradeTypeName'])
        if NOISE_UPGRADE.search(name) or any(name == row[0] for row in out):
            continue
        span, source = research_time(name)
        if source != 'unknown':
            out.append((name, event['_gameloop'], span))
    return out


def window(done, span, chrono):
    """Where the press that started a research has to be.

    Chrono Boost is the only thing in the game that speeds a research up and it
    caps at +50%; nothing slows one down. It is also Protoss only, so for the
    other two races the press sits on the table value and the window is barely
    wider than the rounding in it — which is the point: opening it a third of
    the way for a race that cannot use it lets every unrelated keypress in that
    stretch pass for the research, and one of them will be picked.
    """
    quick = span / CHRONO_MAX if chrono else span / SPAN_SLACK
    return done - span * SPAN_SLACK, done - quick


def research_abilities(replays_and_players):
    """Which numbered ability orders which research, decided over every replay
    at once.

    One replay often cannot tell. A dozen abilities get pressed once in a game,
    and any of them landing inside the window explains the research just as
    well as the real one — 돌진 had four such candidates in one replay, and
    picking among them by how few times they were pressed is a coin toss that
    silently produces a wrong time. Across replays the coincidences do not
    repeat and the real ability does, so the answer is the intersection.

    A single replay still resolves whatever it can, and what it cannot falls
    back to the table and is reported. Convert a folder rather than one file
    and the rest resolve too.
    """
    seen = collections.defaultdict(list)
    for replay, player in replays_and_players:
        presses = command_presses(replay, player)
        if not presses:
            continue
        chrono = RACE_CODE.get(player['race']) == 'P'
        for name, done, span in upgrades_of(replay, player):
            first, last = window(done, span, chrono)
            seen[name].append({key for key, loops in presses.items()
                               if all(first <= loop <= last for loop in loops)})

    narrowed = {name: set.intersection(*sets)
                for name, sets in seen.items() if sets}

    # One ability orders one research, so a key another research has already
    # claimed is not this one's, and resolving the certain ones first can leave
    # a single candidate behind for the rest.
    out, taken = {}, set()
    settled = False
    while not settled:
        settled = True
        for name, keep in narrowed.items():
            if name in out:
                continue
            free = [key for key in keep if key not in taken]
            # Two abilities equally able to explain it means neither is
            # identified, and a build order with the wrong time on it is worse
            # than one that falls back to the table and says so.
            if len(free) != 1:
                continue
            # Two researches down to the same last candidate cannot both be it,
            # and there is nothing to say which. Claiming it for whichever came
            # first in the list is a coin toss, so neither gets it.
            if any(other != name and free[0] in rest and
                   len([key for key in rest if key not in taken]) == 1
                   for other, rest in narrowed.items() if other not in out):
                continue
            out[name] = free[0]
            taken.add(free[0])
            settled = False

    # What these replays proved wins; the seed only fills what they could not
    # reach. research_presses() checks either against this replay's own window
    # before using it.
    merged = dict(RESEARCH_ABILITY)
    merged.update(out)
    return merged


def first_finished(replay, player):
    """When each kind of building this player owns was first finished.

    An add-on answers to more than one name — a Tech Lab is `BarracksTechLab`
    while it sits on a Barracks and plain `TechLab` once it is detached — so
    every spelling reports the earliest of them. Otherwise a floor keyed on
    `BarracksTechLab` misses the Tech Lab that was finished on a Starport and
    walked over later.
    """
    out, init = {}, {}
    for event in replay.tracker:
        kind = event['_event'].rsplit('.', 1)[-1]
        if kind == 'SUnitInitEvent' and event.get('m_controlPlayerId') == player['id']:
            init[event['m_unitTagIndex']] = txt(event['m_unitTypeName'])
        elif kind == 'SUnitDoneEvent' and event['m_unitTagIndex'] in init:
            name = init[event['m_unitTagIndex']]
            done = event['_gameloop']
            spellings = {name}
            attached = ADDON_ON.match(name)
            if attached:
                bare = attached.group(2)
                spellings |= {bare, 'Barracks' + bare, 'Factory' + bare,
                              'Starport' + bare}
            for spelling in spellings:
                if done < out.get(spelling, done + 1):
                    out[spelling] = done
    return out


def train_presses(replay, player, born):
    """The loop each trained unit was actually ordered on.

    The same problem researches had, and the same answer. A unit's step is
    currently its birth less a build time, which is right until the building
    was Chrono Boosted — and a 거신 under boost arrives nearly twenty seconds
    before the table says it could, which drags its step that far back past
    whatever else was happening.

    Presses pair to births first in, first out, the way a production queue
    works. A queued unit therefore reports the moment it was queued rather than
    the moment the building got to it, which is the more useful of the two: it
    is what the player did, and it keeps two units ordered together on one line
    instead of splitting them a build time apart.
    """
    out = {}
    presses = command_presses(replay, player)
    owns = first_finished(replay, player)
    for name, loops in born.items():
        key = TRAIN_ABILITY.get(name)
        if key is None or key not in presses:
            continue
        # The ability says which building it belongs to; if that building was
        # never finished this player cannot have ordered from it, and the id no
        # longer means what the table says. The table time takes over.
        made_in = producer_of(name)
        if made_in not in STARTING and made_in not in owns:
            continue
        cmd = sorted(presses[key])
        # Paired birth by birth rather than all or nothing. A press can be
        # missing — a unit restarted after a cancel, a command the replay did
        # not keep — and giving up on the whole unit for one gap throws away
        # the presses that were there. A birth left without one keeps its
        # table time.
        picked, at = {}, 0
        for birth in sorted(loops):
            # Only commands too old to belong to anything are dropped. A command
            # that sits after this birth belongs to a later one, and skipping it
            # here used to run the pointer off the end: one early pairing, then
            # nothing. 광전사 lost sixteen of seventeen presses that way, because
            # a warp-in arrives thirteen seconds after its press where training
            # takes twenty-seven, so the two interleave.
            while at < len(cmd) and birth - cmd[at] > MAX_GAP:
                at += 1
            if at >= len(cmd):
                break
            if cmd[at] >= birth:
                continue
            # A gap shorter than this is not this birth's press: Chrono Boost
            # caps at +50%, so nothing arrives sooner than that.
            if birth - cmd[at] >= MIN_GAP:
                picked[birth] = cmd[at]
                at += 1
        # Selecting four Gateways and pressing once makes four Zealots off a
        # single command. Pairing one press to one birth can only speak for the
        # first of them, and the rest looked pressless and fell back to a table
        # subtraction — which is most of why units trailed buildings so badly.
        #
        # They are recognisable without guessing: units started by one keypress
        # finish within a moment of each other, where a queue spaces them out by
        # a whole build time. So a birth with no press of its own takes the
        # press of a birth it landed beside.
        for birth in sorted(loops):
            if birth in picked:
                continue
            beside = [b for b in picked if abs(b - birth) <= SAME_PRESS]
            if beside:
                picked[birth] = picked[min(beside, key=lambda b: abs(b - birth))]
        if picked:
            out[name] = picked
    return out


def research_presses(replay, player, abilities):
    """The loop each research was actually ordered on, where it is known.

    Subtracting the research time from the completion event is only right when
    the research ran at normal speed. Chrono Boost shortens the research but
    not the table, so the step lands up to a third of the research time early —
    far enough to put 점멸 at 3:24 off a 황혼 의회 that is not finished until
    3:53. The press has no such problem, and it is the moment the player acted,
    which is the only thing a build order is a list of.

    The window is checked again here rather than trusted from the pooled pass:
    ability ids are game data and a patch may renumber them, and a press that
    no longer fits is one this replay should not use.
    """
    out = {}
    if not abilities:
        return out
    presses = command_presses(replay, player)
    for name, done, span in upgrades_of(replay, player):
        key = abilities.get(name)
        if key is None:
            continue
        first = done - span * TRUST_SPAN[1]
        last = done - span * TRUST_SPAN[0]
        fits = [loop for loop in presses.get(key, []) if first <= loop <= last]
        if fits:
            out[name] = min(fits)
    return out


def steps_for(replay, player, derived, abilities, extras=None):
    """Every step, timed at the moment it was started.

    @param extras  which optional kinds to include: 'chrono', 'mule', 'swap'.
      Morphs are not optional — without them a Terran build never mentions
      궤도 사령부 and a Zerg build never mentions 번식지.
    """
    extras = extras or set()
    steps = []
    unknown = set()

    owner, _ = unit_tags(replay)
    finished = first_finished(replay, player)
    presses = research_presses(replay, player, abilities)
    steps += morph_steps(replay, player, owner)
    if 'swap' in extras:
        steps += swap_steps(replay, player, owner)
    if 'mule' in extras:
        steps += mule_steps(replay, player)
    if 'chrono' in extras:
        steps += chrono_steps(replay, player)

    for event in replay.tracker:
        kind = event['_event'].rsplit('.', 1)[-1]
        loop = event['_gameloop']
        if kind == 'SUnitInitEvent' and event.get('m_controlPlayerId') == player['id']:
            name = txt(event['m_unitTypeName'])
            if not NOISE.match(name):
                # Placement, or the start of a warp-in. Already the press.
                steps.append({'loop': loop, 'name': name, 'kind': 'built',
                              'source': 'event'})
        elif kind == 'SUpgradeEvent' and event['m_playerId'] == player['id']:
            name = txt(event['m_upgradeTypeName'])
            if loop > 0 and not NOISE_UPGRADE.search(name):
                at = presses.get(name)
                if at is not None:
                    steps.append({'loop': at, 'name': name, 'kind': 'upgrade',
                                  'source': 'event'})
                    continue
                span, source = research_time(name)
                if source == 'unknown':
                    unknown.add(name)
                key = RESEARCH_ABILITY.get(name)
                floor = finished.get(RESEARCH_STRUCTURE.get(key[0])) if key else None
                steps.append({'loop': max(floor or 0, loop - span), 'name': name,
                              'kind': 'upgrade', 'source': source})

    born = births(replay, player)
    trained = train_presses(replay, player, born)
    for name, loops in born.items():
        pressed = trained.get(name) or {}
        span, source = build_time(name, derived)
        if source == 'unknown' and len(pressed) < len(loops):
            unknown.add(name)
        made_in = producer_of(name)
        floor = 0 if made_in in STARTING else finished.get(made_in, 0)
        for loop in loops:
            at = pressed.get(loop)
            steps.append({'loop': at if at is not None else max(floor, loop - span),
                          'name': name, 'kind': 'unit',
                          'source': 'event' if at is not None else source})

    steps.sort(key=lambda s: s['loop'])
    return steps, unknown



def note(step, terms, missing):
    """The `// 메모` for a step, or None.

    Only Chrono Boost has one: the building it went on. It reads better as a
    note than folded into the action, and it keeps the action column matching
    a dictionary term so the step still gets its picture.
    """
    if step['kind'] == 'detach':
        # `…에서 분리` beside `…에서 가져옴` makes the two halves of a swap read
        # as a matched pair, and keeps the verb in the note for both.
        came = korean_for(step['from'], terms) if step.get('from') else None
        if came is None and step.get('from'):
            missing.add(step['from'])
            came = step['from']
        return '%s에서 분리' % came if came else '분리'
    if step['kind'] == 'swap':
        # 가져옴 rather than 맞바꿈 or 교환: those claim a trade, which is wrong
        # when a building gives up its add-on and never lands again. This one
        # is true either way.
        #
        # Named even when it came from the same kind of building — 우주공항
        # 기술실 ← 우주공항 reads oddly, but a blank note on one line among
        # several that name their source reads as a gap.
        origin = step.get('from')
        came = korean_for(origin, terms) if origin else None
        if came is None and origin:
            missing.add(origin)
            came = origin
        return '%s에서 가져옴' % came if came else '스왑'
    if step['kind'] != 'chrono' or not step.get('on'):
        return None
    on = korean_for(step['on'], terms)
    if on is None:
        missing.add(step['on'])
        on = step['on']
    return on


def label(step, terms, buildings, missing):
    korean = korean_for(step['name'], terms)
    if korean is None:
        missing.add(step['name'])
        korean = step['name']

    if step['kind'] == 'detach':
        # The bare add-on; what happened to it is in the note.
        return korean
    if step['kind'] == 'chrono':
        # Which building it went on belongs in the note, not here. The action
        # column is what the icon lookup matches, and `인공제어소에 시간 증폭`
        # matched the Cybernetics Core — so a chrono cast drew the icon of a
        # building it did not build.
        return korean
    if step.get('filler'):
        return '%s 계속 생산' % korean
    if step['count'] > 1:
        return '%s %d%s' % (korean, step['count'],
                            '개' if korean in buildings else '기')
    return korean


def build_text(replay, player, derived, abilities, terms, buildings,
               limit=None, extras=None):
    """The build file's text, plus what went into it.

    @returns (lines, step count, {time source: n}, unknown names, no-build-time names)
    """
    steps, unknown = steps_for(replay, player, derived, abilities, extras)
    if limit:
        steps = [s for s in steps if s['loop'] <= limit]
    rows = collapse(steps)
    curve = supply_curve(replay, player)

    missing = set()
    opponents = [p for p in replay.players() if p['id'] != player['id']]
    lines = [
        'name: %s' % os.path.splitext(os.path.basename(replay.path))[0],
        'race: %s' % RACE_CODE.get(player['race'], '?'),
        'vs: %s' % (RACE_CODE.get(opponents[0]['race'], '*') if opponents else '*'),
        'notes: 리플레이 추출 · SC2 %s · %s · %s · %d:%02d' % (
            replay.version, txt(replay.details['m_title']), player['name'],
            replay.seconds // 60, replay.seconds % 60),
        '',
    ]
    for step in rows:
        food = supply_at(curve, step['loop'])
        memo = note(step, terms, missing)
        lines.append(('%s %s %s%s' % (
            clock(step['loop']).ljust(5),
            ('@%d' % food if food is not None else '').ljust(4),
            label(step, terms, buildings, missing),
            '  // %s' % memo if memo else '')).rstrip())

    # Counted over the lines actually written, not the units behind them: with
    # `추적자 3기` on one line, counting units made the sources add up to far
    # more than the step count and the report unreadable.
    sources = collections.Counter(r['source'] for r in rows)
    return lines, len(rows), sources, missing, unknown


def choose(players, want):
    if want is None:
        humans = [p for p in players if p['human']]
        return humans[0] if humans else players[0]
    if want.isdigit():
        for player in players:
            if player['id'] == int(want):
                return player
        raise SystemExit('그런 번호의 플레이어가 없습니다: %s' % want)
    for player in players:
        if want.lower() in player['name'].lower():
            return player
    raise SystemExit('이름이 맞는 플레이어가 없습니다: %s' % want)


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def emit_json(payload):
    """The app's side of this script.

    stdout is the channel, so nothing else may be printed on it — the caller
    parses the whole stream. Windows consoles default to cp949, hence the
    explicit encode rather than print().
    """
    raw = json.dumps(payload, ensure_ascii=False)
    sys.stdout.buffer.write(raw.encode('utf-8'))
    sys.stdout.buffer.flush()


def run_json(args):
    """--json: everything comes back on stdout, nothing is written to disk.

    The app has its own places for builds and shows its own report, so writing
    files here would just leave a second copy going stale.
    """
    loaded = [Replay(path) for path in expand(args.replays)]

    if args.list:
        return emit_json({'ok': True, 'replays': [{
            'file': replay.path,
            'name': os.path.splitext(os.path.basename(replay.path))[0],
            'version': replay.version,
            'map': txt(replay.details['m_title']),
            # Floored, matching the `notes:` line the build file gets, so the
            # panel and the file never disagree about the same game's length.
            'seconds': int(replay.seconds),
            'fellBackTo': replay.fell_back_to,
            'players': replay.players(),
        } for replay in loaded]})

    picked = [(replay, choose(replay.players(), args.player)) for replay in loaded]
    derived = derive_build_times(picked)
    abilities = research_abilities(picked)
    terms = load_terms(args.repo)
    buildings = load_buildings(args.repo)
    limit = args.minutes * 60 * LOOPS if args.minutes else None

    out = []
    for replay, player in picked:
        lines, count, sources, missing, unknown = build_text(
            replay, player, derived, abilities, terms, buildings, limit,
            args.extras)
        out.append({
            'file': replay.path,
            'name': os.path.splitext(os.path.basename(replay.path))[0],
            'player': player,
            'text': '\n'.join(lines) + '\n',
            'steps': count,
            'sources': dict(sources),
            'missing': sorted(missing),
            'noBuildTime': sorted(unknown),
        })
    return emit_json({'ok': True, 'builds': out, 'derived': [{
        'unit': unit,
        'seconds': round(row['build'] / LOOPS, 1),
        'table': BUILD_TIME.get(unit),
        'count': row['count'],
    } for unit, row in sorted(derived.items())]})


def main():
    ap = argparse.ArgumentParser(description='리플레이에서 빌드오더를 뽑습니다.')
    ap.add_argument('replays', nargs='+', help='리플레이 파일, 와일드카드, 또는 폴더')
    ap.add_argument('--out', help='결과를 넣을 폴더 (--json 이면 필요 없습니다)')
    ap.add_argument('--player', help='플레이어 번호 또는 이름 일부 (기본: 사람)')
    ap.add_argument('--list', action='store_true', help='플레이어만 보여주고 끝냅니다')
    ap.add_argument('--minutes', type=float, help='앞 N분까지만')
    ap.add_argument('--chrono', action='store_true',
                    help='시간 증폭을 쓴 시각과 대상도 넣습니다 (프로토스)')
    ap.add_argument('--mule', action='store_true',
                    help='지게로봇을 부른 시각도 넣습니다 (테란)')
    ap.add_argument('--swap', action='store_true',
                    help='애드온 스왑도 넣습니다 (테란)')
    ap.add_argument('--json', action='store_true',
                    help='사람이 읽는 파일 대신 JSON 을 stdout 으로 (앱이 씁니다)')
    ap.add_argument('--repo', default=REPO,
                    help='용어 사전과 아이콘 매니페스트를 찾을 곳')
    args = ap.parse_args()
    args.extras = {name for name in ('chrono', 'mule', 'swap')
                   if getattr(args, name)}

    if args.json:
        # The app gets a message it can show, not a traceback it cannot.
        try:
            return run_json(args)
        except SystemExit as err:
            return emit_json({'ok': False, 'message': str(err)})
        except Exception as err:
            return emit_json({'ok': False,
                              'message': '%s: %s' % (type(err).__name__, err)})
    if not args.out:
        raise SystemExit('--out 으로 결과를 넣을 폴더를 지정하세요.')

    os.makedirs(args.out, exist_ok=True)
    loaded = [Replay(path) for path in expand(args.replays)]

    if args.list:
        report = []
        for replay in loaded:
            report.append('%s  (SC2 %s, %s)' % (
                os.path.basename(replay.path), replay.version,
                txt(replay.details['m_title'])))
            for player in replay.players():
                report.append('   %d. %-24s %-8s %s %s' % (
                    player['id'], player['name'], player['race'],
                    '사람' if player['human'] else 'AI',
                    '승' if player['won'] else '패'))
        write_lines(os.path.join(args.out, '_players.txt'), report)
        return

    picked = [(replay, choose(replay.players(), args.player)) for replay in loaded]
    derived = derive_build_times(picked)
    abilities = research_abilities(picked)
    terms = load_terms(args.repo)
    buildings = load_buildings(args.repo)
    limit = args.minutes * 60 * LOOPS if args.minutes else None

    report = ['리플레이에서 유도한 빌드 시간 (표 대신 이 값을 씀):']
    for unit in sorted(derived):
        row = derived[unit]
        table = BUILD_TIME.get(unit)
        report.append('  %-20s %-11s %5.1f초  (표 %s)  %d기 %d판' % (
            unit, str(row['ability']), row['build'] / LOOPS,
            '%d초' % table if table else '없음', row['count'], row['replays']))
    report.append('')

    for replay, player in picked:
        name = os.path.splitext(os.path.basename(replay.path))[0]
        lines, count, sources, missing, unknown = build_text(
            replay, player, derived, abilities, terms, buildings, limit,
            args.extras)
        write_lines(os.path.join(args.out, name + '.txt'), lines)
        report.append('%s  <- %s (%s)' % (name, player['name'], player['race']))
        if replay.fell_back_to:
            report.append('   ⚠ 이 패치(%s)의 해독표가 없어 %s 것으로 읽었습니다'
                          % (replay.version, replay.fell_back_to))
        report.append('   %d단계 · 시각 출처 %s' % (count, dict(sources)))
        if unknown:
            report.append('   빌드 시간을 모르는 것 (완성 시각 그대로): %s'
                          % ', '.join(sorted(unknown)))
        if missing:
            report.append('   사전에 없는 이름: %s' % ', '.join(sorted(missing)))

    write_lines(os.path.join(args.out, '_report.txt'), report)


if __name__ == '__main__':
    main()
