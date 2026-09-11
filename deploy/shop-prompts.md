# Document your glass shop with AI — starter prompts

A hot shop is a system: gas, air, power, heat, exhaust, and the controls that tie
them together. Documenting yours — as a scene sketch, an electrical one-line, a
piping/instrumentation (P&ID) diagram, and a **bill of materials with real part
numbers** — makes it far easier to *fix, order, insure, and teach*. You don't have to
draw any of it by hand: describe your shop to an AI in plain language and have it
generate the diagrams and the parts list. These prompts get you started.

> **Safety first.** These diagrams are **schematic documentation, not engineering
> drawings**. Gas and high-current electrical work must be designed, installed, and
> inspected by licensed professionals to your local code. Mark anything you're unsure
> of as **TBD** rather than guessing — the example diagrams do exactly that.

---

## Prompt 1 — Describe the space (start here)
Copy this, then replace the bracketed parts with your shop. Be concrete about
position, size, and what connects to what — the AI draws what you tell it.

> Draw a schematic scene (top-down floor plan, not to scale) of my glass studio.
> The space is **[24 × 24 ft]**. The **glory hole** is in **[one corner]**, with
> **forced/combustion air** running along **[the back wall]** from a **[squirrel-cage
> blower]** in **[a closet outside the building]**, plus **[natural gas from the
> city]**. The **furnace** is **[to the right of the glory hole]**, with **[4 × SiC
> heating elements]** wired back through breakers to an **[SCR]** controlled by a
> **[Novus PID]** with a **[K-type thermocouple]**; the crucible is **[160 lb]**, and
> crucible access uses a **[pneumatic lift]** fed by **[a small quiet air compressor
> in the opposite corner]**. To the right of the furnace are **[three annealers:
> a 40″-tall × 32″ square coil unit on a 60 A breaker, and two Paragon annealers,
> ~36″ and slightly smaller, both on 60 A breakers with mercury 60 A relays, all on
> one Novus controller]**. **Ventilation** above the hot equipment is a **[large
> exhaust motor behind the utility wall]**. The **bench** is **[between the glory hole
> and furnace, facing the equipment]**; the **marver** is **[3′×2′, ~5′ right of the
> bench]**, with a **[viewing area ~8′ back at the wall]**. Label each device, show
> the gas / air / power / exhaust runs in different colors, and mark anything unknown
> as **TBD**. Output as a single clean SVG.

## Prompt 2 — Electrical one-line
> From the scene above, produce an **electrical one-line diagram** as an SVG. Show:
> the service and **main breaker**, the **panelboard**, and each branch — every
> annealer (breaker → mercury contactor → coils) and the furnace feeder
> (**[100 A] feeder → subpanel → two [60 A] breakers → SCR → door interlock →
> SiC elements**). Put amperages on each run; where an ampacity or wire size is
> unknown, label it **TBD, verify with an electrician**. Include a legend.

## Prompt 3 — Piping & instrumentation (P&ID)
> Produce a **P&ID** as an SVG for the fuel/air/exhaust systems: **natural gas**
> (city → meter → regulator → shutoff → glory hole & furnace burners), **propane**
> and **oxygen** if present (tanks → regulators → torch station), **compressed air**,
> **combustion/forced air** (blower → burners), and **exhaust** (hoods → duct →
> exhaust motor → outside). Use standard-ish symbols for valves, regulators, needle
> valves, and instruments; color each medium differently; mark pressures **TBD**
> where unknown; include a legend.

## Prompt 4 — Bill of materials with real part numbers
This is the one that makes a shop *fixable*. Ask for a table, and fill in the
manufacturers/part numbers you actually have (read them off the nameplates).

> Build a **bill of materials** table for the shop above with columns:
> **System · Component · Manufacturer · Part number · Spec · Where to buy · Notes**.
> Include the controls (PID, SCR, thermocouples, relays/contactors), heating elements,
> blowers/motors, gas regulators and valves, and safety interlocks. Use the **real
> manufacturer part numbers** I give you; where I don't have one, leave the part
> number blank and add a note on what to check on the nameplate. Do not invent part
> numbers.

See `SHOP-PARTS.md` for a starter reference of common hot-shop components and the
manufacturers to look at (I-Squared-R and Kanthal for elements, Novus/Watlow/Fuji for
controls, etc.) — a place to begin, not a substitute for your nameplates.

---

## Publishing your shop
When you've got a description + diagrams + BOM, **publish them to the Glass Database**:
in **Explore → Add to the database → Shop**, paste your description, your diagram SVGs,
and your BOM. Everything is reviewed before it appears; contact details stay private.
The point is a shared, open library of how glass shops are actually built — so the
next person can fix theirs, or build one.
