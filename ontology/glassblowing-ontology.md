# A Glassblowing Ontology

*A structured reference for crafting explanations — from batch to polish, with regional dialects.*

---

## 0. How to read this

This is an **ontology**, not a glossary: terms are organized into **classes** (kinds of thing) arranged in `is-a` hierarchies, connected by a small set of named **relations**, and annotated with **properties**. The point is that a term's meaning comes from *where it sits* and *what it connects to*, so an explanation can walk the graph rather than reciting definitions.

Conventions used throughout:

- **Class** — a kind of thing (a `Tool`, a `Process`, a `Defect`). Written in Title Case.
- `is-a` — subclass relation (a Jack `is-a` HandTool).
- `part-of` — mereology (a Bubble `part-of` a Gather during blowing).
- *Relations* linking classes are defined in §2 and used with arrows, e.g. `Marver —enables→ Marvering`.
- **Dialect tags** mark where vocabulary forks: **[EN]** Anglo-American, **[IT]** Venetian/Muranese, **[CZ]** Bohemian, **[SE]** Scandinavian, **[FLAME]** flame/lamp-working subculture, **[SCI]** scientific/industrial. A full concordance is §19.

The nine top-level branches (§3–§18) trace the material's life: it is **composed**, **melted**, **gathered**, **formed** (hot), **decorated**, **molded**, **kiln-worked**, **annealed**, and **cold-worked** — organized by a team, subject to defects, and inflected by tradition.

---

## 1. Upper ontology (top-level classes)

```
Thing
├── Substance
│   ├── GlassType            (soda-lime, lead, borosilicate, …)
│   ├── RawMaterial          (silica, flux, stabilizer, colorant, cullet)
│   └── GlassStock           (cane, rod, frit, powder, billet, murrine, sheet)
├── Property
│   ├── ThermalProperty      (annealing point, strain point, Tg, working range)
│   ├── OpticalProperty      (refractive index, dispersion, birefringence)
│   └── CompatibilityProperty(COE, viscosity match)
├── Equipment
│   ├── HeatingEquipment     (furnace, glory hole, annealer, lehr, torch, kiln)
│   ├── HandTool             (jacks, blocks, shears, marver, paddle, soffietta)
│   ├── Iron                 (blowpipe, punty rod)
│   └── Mold                 (dip, optic, blow, paste, casting mold)
├── Process
│   ├── MeltingProcess       (batch melt, fining, conditioning)
│   ├── GatheringProcess     (gather, chill, neck)
│   ├── HotFormingProcess    (blow, block, jack, marver, transfer)
│   ├── DecorationProcess    (case, overlay, trail, cane pickup, fume)
│   ├── MoldFormingProcess   (mold-blow, dip-mold, optic)
│   ├── KilnProcess          (fuse, slump, cast, pâte de verre)
│   ├── AnnealingProcess     (anneal soak, controlled cool)
│   └── ColdWorkingProcess   (cut, grind, lap, polish, engrave, etch)
├── Artifact                 (vessel, sculpture, cane, murrine, blank)
├── Agent                    (gaffer, servitor, gatherer, assistant, apprentice)
├── Defect                   (seed, stone, cord, devit, check, chill mark)
└── Tradition                (Muranese, Bohemian, Swedish, Studio Glass, …)
```

---

## 2. Relations (object properties)

These are the verbs that connect classes. An explanation is mostly a path through them.

| Relation | Domain → Range | Reading |
|---|---|---|
| `is-a` | Class → Class | subclass / specialization |
| `part-of` | Thing → Thing | component / mereology |
| `composed-of` | GlassType → RawMaterial | recipe membership |
| `has-property` | Substance → Property | e.g. glass `has-property` COE |
| `melted-in` | RawMaterial → HeatingEquipment | batch → furnace |
| `produces` | Process → Artifact | forming → vessel |
| `consumes` | Process → Substance | forming → gather |
| `uses` | Process → Equipment | jacking `uses` Jacks |
| `enables` | Equipment → Process | Marver `enables` Marvering |
| `precedes` | Process → Process | workflow ordering |
| `requires` | Process → Property | fusing `requires` COE-match |
| `causes` | Condition → Defect | COE mismatch `causes` cracking |
| `prevents` | Process → Defect | annealing `prevents` strain fracture |
| `performed-by` | Process → Agent | gathering `performed-by` Gatherer |
| `variant-of` | Term → Concept | dialect mapping (§19) |
| `originates-in` | Technique → Tradition | reticello `originates-in` Murano |

---

## 3. Substance — composition & stock

### 3.1 GlassType (by chemistry)

Glass is an **amorphous solid** — a supercooled liquid frozen without crystallizing. Type is defined by the **network former** plus its **modifiers**.

- **Soda-lime-silica glass** — the workhorse. Silica network, sodium fluxes it down, lime (calcium) stabilizes it against water attack. Most soft glass, window, container, and studio "clear." Long working range, forgiving.
- **Lead glass / lead crystal** — potash-lead-silica; lead oxide raises refractive index and density (weight, "ring," brilliance). Soft, long working range, wonderful for cutting. **[EN]** "crystal" strictly means the lead (or lead-substitute) type.
- **Borosilicate** — boron oxide co-former; very low expansion, high thermal-shock resistance. **[SCI]** labware; **[FLAME]** "boro" or "hard glass," the dominant American flamework medium.
- **Barium / lanthanum / lead-free "crystal"** — high-index optical and eco-crystal formulations replacing lead.
- **Aluminosilicate, opal, ruby/gold glasses, uranium glass** — specialty families defined by their modifier or colorant chemistry.

### 3.2 RawMaterial (batch constituents)

- **Network former** — **silica** (sand, quartz). The skeleton.
- **Flux** — **soda ash** (sodium carbonate), **potash** (potassium carbonate). Lowers melting temperature.
- **Stabilizer** — **lime / dolomite** (calcium, magnesium), **alumina**. Makes the glass durable rather than water-soluble ("water glass").
- **Cullet** — recycled/reused glass added to the batch to ease melting and reduce volatilization.
- **Fining agents** — antimony, arsenic, sulfates ("salt cake"), and modern alternatives; they scavenge bubbles during melt.
- **Colorants & opacifiers** — see §3.3.

### 3.3 Colorant (transition-metal & other oxides)

Color in glass is chemistry plus **furnace atmosphere** (oxidizing vs reducing) and, for some, **striking** (color develops only on reheat).

| Colorant | Typical color | Note |
|---|---|---|
| Cobalt oxide | deep blue | very strong, tiny doses |
| Copper | turquoise (oxidizing) / red (reducing, "copper ruby") | atmosphere-sensitive |
| Iron | green/brown; blue-green | ubiquitous impurity |
| Manganese | purple/amethyst; also "glassmaker's soap" (decolorizer) | dual use |
| Chromium | green | |
| Nickel | brown to purple | |
| Selenium + cadmium | yellow → orange → red ("selenium ruby") | **striking** color |
| Gold (colloidal) | ruby ("gold ruby," cranberry) | **striking**; Purple of Cassius |
| Silver | yellow/amber stain; **fuming** hues | surface & vapor effects |
| Uranium | fluorescent yellow-green ("vaseline") | |
| Tin / fluorine / phosphate | white opal / opaque | **opacifiers** |

### 3.4 GlassStock (pre-made forms the maker buys or pulls)

**Billet** (cast chunk) · **rod** · **cane** (pulled thin rod, often colored/patterned) · **stringer** (very thin cane) · **frit** (crushed glass, graded coarse→fine) · **powder** (finest frit) · **murrine / murrina** (sliced patterned cane cross-sections; "millefiori" when floral) · **sheet / plate** (rolled flat, for kiln work) · **confetti / shards** (thin blown-and-broken flakes).

---

## 4. Property — the physics that governs everything

**Viscosity is the master variable.** Glass has no melting point; it thickens continuously as it cools, and every named "point" below is really a fixed viscosity.

- **Working range** — the viscosity band where the glass is soft enough to shape but stiff enough to hold form. Wide range = "long" glass (soda-lime, lead); narrow = "short" glass (borosilicate).
- **Softening point** — sags under its own weight (~10⁷·⁶ poise).
- **Glass transition (Tg)** — the amorphous-solid/liquid boundary region.
- **Annealing point** — stress relaxes in minutes (~10¹³ poise). The center of the anneal soak.
- **Strain point** — below this, stress is effectively frozen; cool slowly *to* here, then you can move faster.
- **Coefficient of expansion (COE / CTE)** — how much the glass grows per degree. The single most important number for joining glasses: **mismatched COE `causes` cracking**. Common studio families: ~90, ~96, ~104 (soft glass); ~33 (borosilicate). "COE 96 is compatible with COE 96" is a *tested-compatibility* claim, not just an expansion match.
- **Devitrification** — unwanted crystallization (a scummy/hazy skin), the enemy of both kiln work and prolonged reheating.
- **Optical properties** — refractive index (lead & barium raise it), dispersion ("fire"), and **birefringence** — stress made visible under crossed polarizers (the polariscope), the diagnostic for annealing quality.

---

## 5. MeltingProcess — from batch to workable glass

### 5.1 HeatingEquipment (melt & maintain)

- **Furnace** — holds molten glass at working temperature. Subtypes: **pot furnace** (glass in a refractory **pot/crucible**), **tank / day-tank furnace** (glass in a lined tank), **invested pot**.
- **Glory hole** — a separate reheating chamber; the piece is spun in it to keep it hot and mobile between tooling steps. `enables` almost all hot forming.
- **Garage / pickup box / warmer** — auxiliary heated box to park work, pre-warm punties, or hold a pickup of color/cane.
- **Annealer / lehr** — controlled-cooling oven (see §12). Batch **annealer** in a studio; continuous **lehr** in industry.

### 5.2 Melting stages (`precedes` chain)

`Batch charging` → **Melting/founding** (raw batch liquefies) → **Fining / refining** (bubbles rise and clear, aided by fining agents) → **Conditioning / plaining** (temperature evened, glass "settled" and homogeneous) → glass ready to **gather**. A **gob** is a metered lump of conditioned glass (industrial term, also used loosely in the studio).

---

## 6. GatheringProcess — collecting molten glass

- **Iron** — the pipe you gather on. **Blowpipe / blow iron** (hollow, for blown work) vs **punty rod / pontil / ponty** (solid, for holding a piece from its base after transfer). **[EN]** "punty"; also spelled **pontil**, **ponty**, **puntee**.
- **Gather** — the act of, and the mass of, molten glass wound onto the iron from the furnace. A **first gather** is followed by additional **overlay gathers** to build mass or add cased color.
- **Chilling** — briefly cooling the gather's skin (on the marver or in air) so it holds shape before the next step.
- **Necking** — running a groove near the iron with jacks to define where the piece will later be separated (the **jack line** or score line).

---

## 7. HotFormingProcess (blowing branch) — the core craft

### 7.1 The bench and its tools

- **The chair / bench** — the glassblower's workstation with arms (rails) on which the iron is rolled to keep the gather centered. **[EN]** "the chair" *is* metonym for the working team.
- **Jacks / pucellas** **[EN/IT]** — the spring-steel tongs that are the primary shaping tool: necking, opening, defining line. **[IT]** *pucellas* (also anglicized "puke-alas"); **[EN]** "jacks."
- **Blocks** — wet, cupped fruitwood (cherry, applewood) tools for centering and rounding the hot gather; the water flashes to steam and floats the glass.
- **Marver** — a flat steel (historically marble — hence the name) table for rolling, cooling, shaping, and picking up color/frit. `enables` **marvering**.
- **Paddle / battledore** — flat wood or graphite board to flatten a foot or bottom.
- **Shears** — **straight shears** (cut trails, gathers) and **diamond/jack shears** (nip and shape). `uses` in cutting off and trimming.
- **Tweezers / jacks** — pull, pinch, pull points, tool small features.
- **Soffietta / puffer** **[IT]** — a cone-tipped blow tube that seals onto the opening of a piece already transferred to the punty, so you can still inflate it without a blowpipe.
- **Gadget** — a spring clamp that grips a finished **foot** in place of a hot punty, avoiding a punty scar.
- **Wet newspaper (pads) / cork** — a heat-shielded hand tool: folded wet newspaper held in the palm to shape the gather directly. Old, universal, still standard.

### 7.2 Forming steps (typical `precedes` sequence)

`Gather` → **Block/center** → **Marver** → **Blow a starter bubble** (the **parison** [SCI/industrial term for the first inflated form]) → **Reheat** (glory hole) → **Inflate & shape** (blocks, jacks, paddle, wet paper) → **Neck / define jack line** → **Transfer to punty** (attach punty to base, crack off blowpipe at the jack line) → **Open the lip** (reheat, spin, jacks flare the opening; centrifugal force does the work) → **Finish** → **Crack off / knock off into the annealer**.

### 7.3 Named blown techniques

- **Free-blowing / offhand** — shaped in the air by tool and breath alone, no mold. **[EN]** "offhand" (the American Studio Glass touchstone).
- **Mold-blowing** — inflated into a mold for form or texture (§11).
- **Incalmo** **[IT]** — two (or more) separately blown, open-mouthed forms fused rim-to-rim while hot to make crisp color bands; demands matched diameters and virtuoso timing.
- **Cased / overlay** — one color gathered over another (or over clear), building concentric layers. Foundational to Graal, Ariel, sommerso, cameo.
- **Sommerso** **[IT]** — "submerged": thick clear layers over interior color, a mid-century Muranese hallmark.
- **Bit work** — small hot additions (handles, feet, prunts, wings) applied from a **bit gather** brought by an assistant.

---

## 8. HotFormingProcess (solid / sculptural / flame branch)

- **Solid hot sculpting** — building form from solid gathers and bits rather than a bubble; figurative and abstract sculpture.
- **Flameworking / lampworking / torchwork** **[FLAME]** — forming at a bench **torch** rather than a furnace, from rod and tube. Historically "lampworking" (oil-lamp-and-bellows era); now torch-based.
  - **Soft glass** (soda-lime, e.g. COE 104) vs **boro** (borosilicate) subcultures, each with its own aesthetic and vocabulary.
  - Core operations: **rod-to-flame**, **encasing**, **fuming** (depositing silver/gold vapor for iridescent color), **implosion**, **stringer control**, **tube working** (for vessels and scientific ware).
- **Scientific glassblowing** **[SCI]** — precision borosilicate/quartz fabrication: **graded seals**, **T-seals**, **ring seals**, vacuum manifolds, Dewar and joint work. Adjacent domains you may bridge to: **neon/gas-discharge tube bending** (electrode sealing, tubulation, bombarding, gas fill) and **plasma/vacuum vessels** (KF/CF fittings, tubulation, evacuation and back-fill). These share flame/seal vocabulary with §8 while borrowing vacuum terms from §12/§13.

---

## 9. DecorationProcess (hot decoration & color application)

- **Rolling in color** — rolling a hot gather over **frit** or **powder** on the marver, then melting it in. Fine control of density and gradient.
- **Casing / flashing** — a thin (flashing) or thick (casing) surface color layer over a body color; the substrate for later **cameo** cold cutting.
- **Trailing** — laying threads of hot glass onto the surface from a bit.
- **Combing / feathering / festooning** — dragging a hooked tool through trails or bands to create feathered, dragged, or looped patterns (ancient core-formed technique, still used).
- **Cane & murrine pickup** — arranging cane or sliced **murrine** on the marver or in a pickup box and rolling the hot form over them to fuse a pattern into the skin (§10).
- **Powder printing / stenciling**, **metal leaf** (gold/silver/palladium), **mica**, **dichroic** foils and coatings — surface applications fused in with heat.
- **Ghiaccio / "ice glass"** **[IT]** — thermal-shock crackle: plunge the hot gather in water then reheat and reform, sealing a crazed surface.

---

## 10. Cane, Murrine & Filigree (a dense Muranese subdomain)

- **Cane** — pulled colored rod. **Stringer** is its thinnest form.
- **Zanfirico / filigrana** **[IT]** — cane containing twisted internal threads; the family term for lacy thread-in-glass.
  - **Reticello** — two layers of oppositely-twisted cane crossed to make a net, trapping a regular grid of air bubbles at each intersection. A benchmark of control.
  - **Mezza filigrana** — single-twist half-filigree.
  - **Retortoli / ritorti** — twisted-ribbon canes.
- **Murrine / murrina** — patterned cane sliced crosswise to reveal a repeating image; **millefiori** ("thousand flowers") is the floral case. Assembled and fused, then optionally blown out.
- **Latticino / lattimo** **[IT]** — white (milk-glass) thread work / opaque white glass used in filigree.
- **Battuto** **[IT]** — a *cold*-worked "beaten" surface of shallow wheel-cut facets (listed here because it finishes cane/sommerso pieces; mechanically it belongs to §14).

---

## 11. Mold — mold making & mold forming

### 11.1 Mold (by function)

- **Dip mold** — an open one-piece mold; the bubble is dipped and inflated to take a base pattern (ribs, panels).
- **Optic mold** — a ribbed dip mold used specifically to impress an **optic** texture (twist ribs, diamond optic) that is then blown out and often twisted.
- **Blow mold** — a closed, multi-part (hinged) mold for reproducible form; **paste mold** (carbonized, water-swabbed, spins the piece for a seamless polished surface) vs **contact/chill mold** (leaves seams and mold marks).
- **Turn / spun mold** — the piece is rotated inside to erase seams.
- **Casting mold** — refractory/investment mold for kiln casting (§12), typically **lost-wax** in plaster-silica.

### 11.2 Mold materials

Fruitwood and cherry (hand tools & simple molds), **graphite** (marvers, molds, paddles — machinable, releases cleanly, but oxidizes/erodes over time), metal/steel, **plaster-silica investment** (kiln casting), **sand** (historic), and **ceramic-fiber** formers.

---

## 12. KilnProcess — warm / kiln glass

A separate forming family: glass is shaped by *heat and gravity in a kiln* rather than by hand at a furnace. Governed entirely by the **firing schedule**.

- **Fusing** — heating separate pieces of compatible sheet/frit until they bond. **Tack fuse** (pieces stick but keep their shape), **contour fuse** (edges round), **full fuse** (a single flat sheet).
- **Slumping** — a fused blank sags over or into a mold to take a form (a bowl, a plate).
- **Kiln casting** — molten glass fills a refractory mold; includes **lost-wax casting**.
- **Pâte de verre** **[FR/IT-adjacent]** — "glass paste": a paste of frit/powder + binder packed into a mold and fired, giving a granular, luminous wall. Related: **pâte de cristal** (coarser, more translucent).
- **Firing schedule** (the DMN of kiln work): **ramp rate** (°/hr) → **process/top temperature** with a **soak/hold** → controlled drop to the **anneal soak** → slow cool through the strain point → room temperature. Compatibility (COE) and thickness set the anneal times.

---

## 13. AnnealingProcess — stress relief

The step that makes glass survive. Fast, uneven cooling freezes in **residual stress**; the piece may fail immediately or weeks later.

- **Anneal soak** — hold at the **annealing point** long enough to relax stress uniformly through the wall.
- **Controlled cool** — descend slowly *to* the **strain point** (the slow, critical leg), then faster below it.
- **Thermal shock** — failure from a temperature gradient too steep for the glass to accommodate; the acute cousin of annealing failure.
- **Diagnosis** — **birefringence** viewed in a **polariscope** (crossed polarizers) reveals frozen stress as colored fringes. `prevents` relation: proper annealing `prevents` strain fracture.

---

## 14. ColdWorkingProcess — cutting, grinding, polishing, engraving

Everything done to cold, annealed glass with abrasives, wheels, or acid.

- **Cutting (parting)** — **cold saw / diamond band saw / tile saw** to section blanks; **scoring & breaking** for sheet.
- **Grinding & lapping** — flattening and truing on a **flat lap** or grinding wheel with graded diamond/silicon-carbide; **lapping** is the fine, flat stage.
- **Polishing** — bringing to clarity: mechanical (**cerium oxide** or tin oxide on felt/cork wheels, pumice), **fire polishing** (a quick reheat to gloss the surface), or **acid polishing** (hydrofluoric/sulfuric bath — hazardous, industrial for cut crystal).
- **Cutting (decorative, crystal)** **[EN/CZ]** — the lapidary tradition: **mitre cut**, **panel cut**, **brilliant cut**, executed on a **cutting lathe**; the Bohemian heartland.
- **Engraving** — **copper-wheel engraving** (fine intaglio via small copper wheels + abrasive slurry), **stone/diamond-wheel engraving**, **diamond-point** and **stipple** engraving (dotted shading), **intaglio** (cut into) vs **cameo** (cut away a cased layer to reveal ground beneath).
- **Abrasive blasting** — **sandblasting** for frosting, **carving** for depth, stencils for imagery.
- **Etching** — **acid (HF) etching**, **French embossing** (matte acid finish).
- **Battuto** **[IT]** — dense shallow wheel-cutting leaving a hammered texture (see §10).
- **Cold assembly & decoration** — gluing (UV adhesive), enameling/painting, gilding, cold silvering.

---

## 15. Tool index (consolidated `uses` map)

| Tool | Class | Primary process |
|---|---|---|
| Blowpipe / blow iron | Iron | gathering, blowing |
| Punty rod (pontil) | Iron | holding after transfer |
| Jacks / pucellas | HandTool | necking, opening, line |
| Blocks | HandTool | centering, rounding |
| Marver | HandTool/surface | marvering, color pickup |
| Paddle / battledore | HandTool | flattening feet/bases |
| Shears (straight / diamond) | HandTool | cutting, nipping |
| Soffietta / puffer | HandTool | inflating off the pipe |
| Gadget | HandTool | gripping a foot, no scar |
| Wet newspaper / cork | HandTool | direct hand-shaping |
| Tweezers | HandTool | pulling, pinching detail |
| Glory hole | HeatingEquipment | reheating |
| Furnace + pot | HeatingEquipment | melting, holding |
| Annealer / lehr | HeatingEquipment | annealing |
| Torch | HeatingEquipment | flame/lamp working |
| Molds (dip/optic/blow/cast) | Mold | mold forming, casting |
| Lap / cutting wheel / lathe | ColdWorking equip. | grinding, cutting, polish |
| Polariscope | Instrument | stress inspection |

---

## 16. Agent — studio roles & social organization

Glassblowing is a *team* craft; the vocabulary of the team is one of the richest dialect zones.

- **Gaffer** **[EN]** / **Maestro** **[IT]** — the master who sits in the chair and forms the piece.
- **Servitor / servitore** **[EN/IT]** — the first assistant; gathers bits, brings tools, makes feet and additions.
- **Second gaffer / footmaker** — makes and attaches feet and components.
- **Gatherer** — gathers glass from the furnace and preps irons.
- **Bit gatherer** — brings hot "bits" on demand for handles, prunts, additions.
- **Taker-in / carry-in boy** **[EN]** — carries finished work to the lehr.
- **Marver boy / warm-in** — historical junior roles (marvering, keeping irons warm).
- **Apprentice / garzone** **[IT]** — learner working up the ladder.
- **The shop / the chair** — the whole team, named by its workstation.

---

## 17. Defect — faults & their causes (a `causes` map for QC talk)

| Defect | Appearance | Typical cause |
|---|---|---|
| **Seed** | tiny bubble | incomplete fining |
| **Blister** | large bubble | gas release, over-reheat, wet tool |
| **Stone** | solid inclusion | unmelted batch or refractory bit |
| **Cord / striae** | ropy optical streaks | inhomogeneous melt (density variation) |
| **Devitrification (devit)** | hazy/scummy crystalline skin | too long in the crystallization range |
| **Chill mark / chill wrinkle** | surface ripples | glass touched something too cold |
| **Shear mark** | line/notch | shears left a mark |
| **Punty scar** | rough spot on base | punty break-off (avoid with a gadget) |
| **Check** | small surface crack | thermal shock, mishandling |
| **Annealing crack / strain fracture** | clean crack, sometimes delayed | inadequate annealing, COE mismatch |
| **Ream** | glassy layer of differing composition | contamination/inhomogeneity |

---

## 18. Tradition — regional & historical schools

Each tradition bundles a **material bias**, **signature techniques**, and a **dialect**. `originates-in` links techniques back here.

- **Ancient / Roman** — **core-forming** and **casting** precede blowing; **glassblowing invented ~mid-1st century BCE** in the Syro-Palestinian (Levantine) region, industrialized under Rome. Mosaic/**millefiori** roots.
- **Islamic world (medieval)** — luster painting, enameled and gilded mosque lamps, wheel-cut relief; a bridge between antiquity and Venice.
- **Venetian / Muranese** **[IT]** — the technique wellspring: **cristallo** (clear soda glass), **lattimo** (milk glass), **filigrana / zanfirico / reticello**, **avventurina** (copper-crystal sparkle), **calcedonio** (marbled chalcedony imitation), **ghiaccio** (ice glass), **incalmo**, **sommerso**, **battuto**. Guild secrecy on Murano; the maestro/servitore team language.
- **Façon de Venise** — "in the Venetian manner"; émigré Venetian makers spreading the style across Renaissance Europe.
- **Bohemian / Czech** **[CZ]** — potash-lime "Bohemian crystal" hard enough for deep **wheel cutting and engraving**; **hyalith** (dense black/red), **lithyalin** (marbled opaque), rich **cased-and-cut** overlay (the classic ruby-cut-to-clear).
- **English / Anglo-American** **[EN]** — **lead crystal** (Ravenscroft, 1670s) prized for weight and brilliance; the "gaffer / chair / servitor / gatherer / taker-in" team lexicon; cut-glass tradition.
- **Scandinavian (Swedish)** **[SE]** — Orrefors & Kosta refinements of cased/engraved work: **Graal** (a cased, etched/engraved design encased in clear and reblown, ~1916), **Ariel** (patterns of *trapped air* between cased layers, ~1937), plus **Kraka** and **Ravenna**. Restrained modern design idiom.
- **American Studio Glass movement** — **Harvey Littleton** and **Dominick Labino**, Toledo workshops **1962**: a small furnace and workable glass that took blowing out of the factory and into the artist's studio. Popularized **"offhand"** as an artistic value; parent of the contemporary studio scene.
- **Chinese & Japanese** — distinct lineages (Chinese "Peking glass" cased-and-carved snuff bottles; Japanese Edo-kiriko cut glass and contemporary flame/kiln work).

---

## 19. Dialect concordance (cross-reference the same concept)

Use this to switch registers for different audiences.

| Concept | [EN] Anglo | [IT] Muranese | [CZ]/[EU] | [FLAME]/[SCI] |
|---|---|---|---|---|
| Master blower | gaffer | maestro | — | (torch) artist |
| First assistant | servitor | servitore | — | — |
| Shaping tongs | jacks | pucellas | — | (graphite) tools |
| Solid holding rod | punty / pontil / ponty | pontello | — | punty (usage differs) |
| Reheating chamber | glory hole | — | — | (kiln / torch) |
| Cooling oven | annealer / lehr | — | — | kiln / annealer |
| No-mold blowing | offhand / free-blown | soffiato a mano | — | — |
| Twisted-thread cane | filigree / lace glass | filigrana / zanfirico | — | — |
| Net-cane w/ trapped air | — | reticello | — | — |
| Air-pattern casing | air-trap | — | (Ariel, [SE]) | encasing |
| Submerged color layers | cased / layered | sommerso | überfang (overlay, DE) | encased |
| Milk glass | milk glass / opal | lattimo | — | — |
| Sparkling copper glass | goldstone / aventurine | avventurina | — | — |
| Ice-crackle finish | crackle / ice glass | ghiaccio | — | — |
| Beaten wheel texture | (battuto) | battuto | — | — |
| Hard, low-expansion glass | borosilicate | — | — | boro / hard glass |
| Ordinary blowing glass | soft glass / soda-lime | — | — | soft glass (COE 104) |
| Glass batch chunk | billet | — | — | rod / tube |
| Bubble (defect) | seed / blister | — | — | seed |

*Notes on forks:* **"punty"** everywhere names the base-holding rod, but flameworkers also use "punty" for a temporary handle rod on small work — same word, narrower sense. **"Crystal"** is a chemistry claim in **[EN]** (lead/lead-substitute) but a loose synonym for "fine clear glass" in casual speech — worth disambiguating in any explanation. **"Overlay," "casing," "flashing"** name the same layering by *thickness and intent*: flashing thin, casing thick, overlay generic.

---

## 20. Using this for explanations (and where to take it next)

Three moves make explanations from this graph:

1. **Locate** the term in its class (§1) — is the audience asking about a *substance*, a *tool*, a *process*, or a *defect*? That fixes the frame.
2. **Walk one relation out** (§2) — most "why" questions are answered by a single hop: *why did it crack?* → `COE mismatch —causes→ strain fracture`, `annealing —prevents→` it. *What's this tool for?* → `Jacks —uses-in→ necking, opening`.
3. **Pick the register** (§19) — say "gaffer" to an English studio, "maestro" on Murano, "the chair" when you mean the team.

**Natural extensions if you want them:**
- A **machine-readable version** (Turtle/RDF or a Wikidata-style entity list with QIDs) so this can live alongside your Lexipedia/BPMN work and be queried rather than read.
- Deepening any single branch into its own model — the **firing-schedule** as a proper decision model, **defect→cause** as a diagnostic tree, or the **melting/fining chemistry** with reaction detail.
- Adjacent domains you already work in — **neon/gas-discharge** and **plasma-vessel** fabrication — spun out as sibling ontologies that share the flame/seal and vacuum vocabulary.

Tell me which branch to go deeper on or whether to render a formal/queryable version, and I'll build it out.
