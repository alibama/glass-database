# Brick Schema for hot shops: is it the right way to describe our equipment?

*An assessment, with a draft vocabulary and a worked model. November 2026.*

**Short answer.** Brick is a good fit for one layer of what we want to share and the wrong
tool for two others. It describes a studio *as it operates*: which equipment exists, what it
is made of, what feeds it (gas, air, power), what it measures and controls, where it stands,
and which systems it belongs to. That is the layer two studios, a kiln controller, an
archivist and a grant reviewer need in common, and Brick gives it a stable, open, queryable
vocabulary with free validation tools. It has no words for glass equipment at all, so we add
forty-six classes (a furnace, a glory hole, an annealer, a burner, a gas train, a flame
safeguard, a crucible, a silicon-carbide element…) and five substances (propane among them)
as subclasses of Brick's own; that is [docs/brick/hotshop.ttl](brick/hotshop.ttl), and
[docs/brick/example-hot-shop.ttl](brick/example-hot-shop.ttl) describes the three builds of
[the build page](https://glassdatabase.org/shop-build.html) with it. Brick does **not**
describe a design (dimensions beyond a few, materials, refractory layers, parts, prices) or
a process (melt cycles, annealing schedules, batch recipes); for those the parts JSON and
the controller's own programme remain the record, and the model points at them.

## 1. What Brick is

[Brick](https://brickschema.org) is an open ontology for buildings, maintained by the Brick
Consortium and published under the BSD 3-clause licence. Version 1.5.0 was released on
29 October 2026 ([release notes](https://github.com/BrickSchema/Brick/releases/tag/v1.5.0));
the ontology file carries 1,470 Brick classes, 185 of them deprecated. It is written in RDF
and OWL with SHACL shapes, the same stack as the wiki's SPARQL endpoint, so the tools we
already use for the Glass Database (RDF, SPARQL, Turtle files in git) apply unchanged.

Four kinds of thing, and relationships between them:

- **Equipment** (`brick:Equipment` and some 300 subclasses: fans, valves, meters, panels,
  breakers, relays, controllers, hoods, tanks…), composed with `brick:hasPart`.
- **Points** (`brick:Point`: Sensor, Setpoint, Command, Status, Alarm, Parameter, about
  900 subclasses), attached with `brick:hasPoint` and, since 1.5, hosted by a
  `brick:Controller` with `brick:hosts`; a controller `brick:controls` equipment.
- **Locations**: since 1.4 the RealEstateCore space classes bundled with Brick (`rec:Site`,
  `rec:Building`, `rec:Level`, `rec:Room`, `rec:OutdoorSpace`); Brick's own location classes
  remain as deprecated aliases. Things stand in them with `brick:hasLocation`.
- **Collections**: `brick:System` (Gas_System, Electrical_System, Safety_System…), loops,
  plants and point collections, with members through `rec:includes`.

Flows go along `brick:feeds`; what flows is a **Substance** (`brick:Natural_Gas`, `Air`,
`Exhaust_Air`, `Methane`…) named with `brick:hasInputSubstance` / `hasOutputSubstance`;
consumption is tied to meters with `brick:meters` and `brick:hasSubMeter`. Ratings are
**entity properties** whose values carry a QUDT unit: `brick:ratedPowerInput`,
`ratedVoltageInput`, `ratedCurrentInput`, `electricalPhaseCount`, `electricalPhases`,
`currentFlowType`, `volume`, `area`, `thermalTransmittance` and a few dozen more. Every
class has a one-sentence definition, and a free Python package
([brickschema](https://pypi.org/project/brickschema/)) validates a model against the
ontology's shapes.

Two neighbours matter. **RealEstateCore** (REC) is merged into the Brick distribution and
adds asset facts (manufacturer and the like) and a long list of room types. **ASHRAE
Standard 223P** is the committee standard Brick is converging on; it models connection
points and media more precisely than Brick's `feeds`, which would suit gas trains and
three-phase feeds well. As of the drafts I could find it is still in ASHRAE public review
([June 2025 draft](https://osr.ashrae.org/Online-Comment-Database/ShowDoc2/Table/DocumentAttachments/FileName/4627-223p-PPR1-Draft-1_chair_approved.pdf/download/false),
[DOE project page](https://www.energy.gov/cmei/buildings/ashrae-standard-223p)), and Brick
1.5 updated its 223P usage and examples. Nothing in this proposal blocks a move to 223P
later; the class names would map one to one.

## 2. What fits, straight out of Brick

Checked against the 1.5.0 ontology file, class by class. Our hot shop needs these and Brick
has them:

| Hot shop thing | Brick class | Note |
|---|---|---|
| Canopy hood, exhaust fan, make-up air | `Fume_Hood` (a subclass of `Exhaust_Fan`), `Exhaust_Fan`, `Makeup_Air_Unit` | `Fume_Hood_Air_Flow_Sensor` exists; a burner's air valve is not a `Damper` (see below) |
| Gas meter, house line, regulator, shutoff valves | `Building_Gas_Meter`, `Gas_Distribution`, `Gas_Pressure_Regulator_Valve`, `Gas_Valve`, `Natural_Gas_Seismic_Shutoff_Valve` | `Gas_System` collects them |
| Panel, breakers, disconnect, contactor, meter | `Breaker_Panel`, `Circuit_Breaker`, `Disconnect_Switch`, `Relay`, `Electrical_Meter` | `electricalPhaseCount`, `ratedVoltageInput`, `ratedCurrentInput` carry single- or three-phase |
| Blower motor, VFD | `Fan`, `Motor`, `Variable_Frequency_Drive` | |
| PID controller, flame safeguard, high limit | `Controller` (new in 1.5), with `hosts` and `controls` | subclassed below to say which is which |
| Thermocouple probe, CO detector, pressure switch | `Sensor_Equipment`, `IAQ_Sensor_Equipment` | the reading is a separate Point |
| Temperatures, setpoints, output, alarms | `Temperature_Sensor`, `Temperature_Setpoint`, `Max_Temperature_Setpoint_Limit`, `Heating_Command`, `High_Temperature_Alarm`, `PID_Parameter` | |
| Burner management states | `Lockout_Status`, `Low_Pressure_Alarm`, `High_Pressure_Alarm` (new in 1.5), `Air_Flow_Loss_Alarm`, `On_Off_Command`, `Fan_On_Off_Status`, `Operating_Mode_Status` | |
| Doors, stops | `Open_Close_Status`, `Open_Close_Command`, `Emergency_Push_Button_Status`, `Emergency_Power_Off_System` | |
| Energy and gas use | `Electric_Power_Sensor`, `Electrical_Energy_Usage_Sensor`, `Current_Sensor`, `Natural_Gas_Usage_Sensor`, `Natural_Gas_Flow_Sensor`, `Pressure_Sensor` | QUDT has every unit we use: °F, kW, A, V, BTU/h, in WC, CFM, ft³, lb, in |
| Fire and first aid | `Heat_Detector`, `Smoke_Detector`, `Fire_Safety_Equipment`, `Eye_Wash_Station`, `First_Aid_Kit`, `Safety_System` | |
| The room, the yard, the building | `rec:Room`, `rec:OutdoorSpace`, `rec:Building`, `rec:Site` | RealEstateCore classes, bundled with Brick; Brick's own `Room`, `Site` and the rest are deprecated since 1.4 |
| Fuel | `Natural_Gas`, `Methane`, `Air`, `Exhaust_Air`, `Outside_Air` | |
| A propane tank | `Storage_Tank` | |

## 3. What is missing, and how the vocabulary fills it

Brick's authors wrote for offices and labs. Searching the ontology for *kiln, furnace,
burner, crucible, flame, thermocouple, combustion, refractory, anneal* finds nothing (the
word "furnace" occurs once, in the definition of return air). So:

| Gap | What we add in `hotshop:` | Parent in Brick |
|---|---|---|
| The equipment itself | `Glass_Furnace` (`Electric_`, `Gas_`), `Glory_Hole`, `Kiln` → `Annealer`, `Garage_Kiln`, `Pipe_Warmer` | `brick:Equipment` via `Glass_Heating_Equipment` |
| Heat sources | `Burner` (`Forced_Air_`, `Venturi_`, `Pilot_`), `Combustion_Air_Blower`, `Air_Valve`, `Igniter`, `Heating_Element` (`Silicon_Carbide_`, `Wire_`) | `Equipment`, `Fan`, `Valve`, `Electrical_Equipment` |
| Power and control | `Power_Controller` (SCR), `Solid_State_Relay`, `Contactor`, `Temperature_Controller`, `High_Limit_Controller`, `Flame_Safeguard` | `Electrical_Equipment`, `Relay`, `Controller` |
| Sensing devices | `Thermocouple`, `Flame_Detector`, `Gas_Pressure_Switch`, `Air_Proving_Switch` | `Sensor_Equipment` |
| Gas | `Gas_Train`, `Manual_Gas_Shutoff_Valve`, `Safety_Shutoff_Valve`, `Gas_Trim_Valve`; substance `Propane` | `Gas_Distribution`, `Gas_Valve`; `brick:Gas` |
| The chamber | `Crucible`, `Chamber_Door`, `Canopy_Hood`, `Air_Compressor`; substances `Combustion_Air`, `Flue_Gas`, `Compressed_Air`, `Molten_Glass` | `Tank`, `Equipment`, `Fume_Hood` |
| Points | `Flame_Status`; `Crown_`, `Glass_`, `Chamber_Temperature_Sensor`; schedule points `Segment_Temperature_Setpoint`, `Ramp_Rate_Parameter`, `Hold_Time_Parameter` | `Status`, `Temperature_Sensor`, `Temperature_Setpoint`, `Parameter` |
| Schedules | `Firing_Schedule` and `Schedule_Segment` as point collections, ordered by `segmentNumber` | `Point_Collection` |
| Safety chain | `hotshop:interlocks` (door switch → contactor, air switch → safety valve, hood → gas) | `brick:Relationship` |
| Ratings Brick lacks | `ratedMaximumTemperature`, `ratedGlassCapacity`, `boreDiameter`, `interiorWidth` / `Depth` / `Height`, `thermocoupleType` | entity properties with unit shapes |

Three points of judgement in the mapping, so nobody is surprised later:

- A kiln coil is **not** a `brick:Heating_Coil`. Brick's coil is an HVAC heat exchanger; a
  tool that sees one will look for water. Elements are their own class under `Equipment`.
- A crucible is a `brick:Tank` ("a container designed to hold or store fluids"). Close
  enough, and it brings `volume` with it.
- A canopy hood is a `brick:Fume_Hood`, which Brick defines as a laboratory enclosure and
  files under exhaust fans. The subclass says "open canopy"; the parent keeps the flow sensor
  and the exhaust semantics.
- A burner's butterfly air valve is a `brick:Valve`, not a `brick:Damper`. Brick's shapes
  only let a damper feed air-handling equipment and spaces; the validator rejected the first
  draft of the example for exactly that, which is the kind of catch the shapes are for.

The vocabulary also carries a **first safety profile**: three SHACL shapes saying that a
main burner must have a manual shutoff and a safety shutoff valve upstream, that gas-fired
glass equipment must include a flame safeguard, and that an electrically heated chamber
must include an independent high limit. A model that fails them is not malformed; it
describes a shop without the safeguard, which is exactly what we would want to see when
comparing models.

## 4. What Brick should not be asked to do

- **The design and the bill of materials.** Wall layers, brick counts, steel sizes, orifice
  drills, prices. Brick has no notion of a part that is not operated. The source of truth
  stays [deploy/shop-build/bom.json](../deploy/shop-build/bom.json); the model carries
  `rdfs:seeAlso` to the build page. If a standard for this layer is ever wanted, IFC's
  material layer sets (and `IfcBurner`) are the nearest fit, and schema.org's Product and
  Offer cover a parts list with prices.
- **Processes.** Melt cycles, charging, the annealing schedule, batch recipes. The schedule
  can be *sketched* as point collections (the example does), which is enough to compare two
  shops' practice, but the controller's programme is the record. Brick has no sequence,
  recipe or event model beyond REC's observation events.
- **Materials and consumables.** Glass, batch, colour, the crucible's life. Those belong in
  the Glass Database's own records.
- **People and procedures.** Who may light the glory hole, the lockout procedure, the
  training log. Not Brick's concern.

## 5. How two parties would use it

1. Each side describes its shop in a Turtle file with Brick plus `hotshop:` (the example is
   a template: copy, rename the `ex:` prefix to your own, delete what you do not have).
2. Each validates it with brickschema against Brick 1.5 and the `hotshop:` shapes:

   ```python
   import brickschema
   g = brickschema.Graph(load_brick=False)
   g.load_file("Brick+imports.ttl")          # from the Brick 1.5.0 GitHub release
   g.load_file("hotshop.ttl")
   g.load_file("my-shop.ttl")
   valid, _, report = g.validate()
   ```

   The two files here validate that way (brickschema 0.8.0, pySHACL 0.40.2, Brick 1.5.0
   with its imports, about two and a half minutes a run): no results on any `hotshop:` or
   `ex:` node. The run's one remaining result is on Brick's own `brick:value` property,
   which Brick's namespace rule flags in any model that carries entity-property values; it
   is theirs to fix, not ours. Use the `Brick+imports.ttl` bundle rather than `Brick.ttl`
   alone, or the validator reports several hundred missing QUDT quantity kinds instead.
   The check is real: a deliberately broken probe (a room attached as a point, a feed into
   a sensor, an undefined Brick class) was caught on every count.
3. Then the same questions get the same answers on both files. Three in SPARQL:

   ```sparql
   # Every gas-fired piece of glass equipment, its fuel, and whether it has a flame safeguard
   SELECT ?equipment ?fuel ?safeguard WHERE {
     ?equipment a/rdfs:subClassOf* hotshop:Glass_Heating_Equipment ;
                brick:hasInputSubstance ?fuel .
     FILTER (?fuel IN (brick:Natural_Gas, hotshop:Propane))
     OPTIONAL { ?equipment brick:hasPart ?safeguard . ?safeguard a hotshop:Flame_Safeguard }
   }
   ```

   ```sparql
   # Thermocouples by type, with the equipment they serve and the point they feed
   SELECT ?equipment ?tc ?type ?point WHERE {
     ?equipment brick:hasPart ?tc . ?tc a hotshop:Thermocouple ;
       hotshop:thermocoupleType/brick:value ?type ; brick:hasPoint ?point .
   }
   ```

   ```sparql
   # The electrical loads and what service they need
   SELECT ?equipment ?kw ?volts ?phases WHERE {
     ?equipment brick:ratedPowerInput/brick:value ?kw ;
                brick:ratedVoltageInput/brick:value ?volts ;
                brick:electricalPhaseCount/brick:value ?phases .
   }
   ```

4. Differences in *vocabulary* become differences in *fact*: one shop's "safety valve" and
   another's "solenoid" are both `hotshop:Safety_Shutoff_Valve`; a controller that is a
   Novus on Bluetooth and one that is a Watlow on Modbus are both a
   `hotshop:Temperature_Controller` that `hosts` the same kinds of points.

## 6. Where this touches our own work

- **The build page.** The example model is the build page said in Brick. Later, the
  generator that already turns `bom.json` into the page and the drawings can emit the
  Turtle too, so the three stay in step; that is the automation we agreed to leave for now.
- **The kiln controller.** A `brick:Controller` that `hosts` points is precisely a device
  with a point list. A controller's device registry could publish the annealer's points in
  this shape and a studio's model would reference them by IRI.
- **The wiki.** Equipment classes are the kind of thing Wikidata has items for; adding
  `owl:sameAs` or `skos:closeMatch` links from `hotshop:` classes to checked Wikidata items
  would let the museum and archive side resolve our terms to theirs. Not done here; the
  item numbers want checking before they go in.
- **Grants and partners.** A one-page model of a studio, validated, is a credible exhibit:
  it shows the safety chain, the loads and the ventilation in a form a reviewer's engineer
  can read with standard tools.

## 7. Verdict and next steps

Adopt Brick for the operational description of studios, with `hotshop:` as our extension,
and say plainly that design and process stay in their own records. Documents only for now,
as agreed:

- Review the class list and definitions in `hotshop.ttl`; names are cheap to change before
  anyone else uses them.
- Decide the namespace for good (`https://glassdatabase.org/ns/hotshop#` is the draft) and
  whether to publish the vocabulary at that address.
- When the first real shop is modelled, add REC asset facts (manufacturer, model, year) and
  the Wikidata links.
- Later: generate the Turtle from `bom.json`, validate it in the test suite, and revisit
  ASHRAE 223P once it is published.

## Sources

- Brick ontology 1.5.0, [release notes](https://github.com/BrickSchema/Brick/releases/tag/v1.5.0)
  and the `Brick.ttl` / `Brick+imports.ttl` files on that release; licence from the
  repository ([BSD 3-clause](https://github.com/BrickSchema/Brick)).
- Brick 1.4 and RealEstateCore harmonisation:
  [announcement](https://groups.google.com/g/brickschema/c/Ree1Jxwd5-c).
- ASHRAE 223P: [DOE project page](https://www.energy.gov/cmei/buildings/ashrae-standard-223p);
  [BSR/ASHRAE 223P public review draft, 2025](https://osr.ashrae.org/Online-Comment-Database/ShowDoc2/Table/DocumentAttachments/FileName/4627-223p-PPR1-Draft-1_chair_approved.pdf/download/false).
- [brickschema](https://pypi.org/project/brickschema/) Python package, used for validation.
