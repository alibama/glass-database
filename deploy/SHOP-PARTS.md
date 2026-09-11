# Hot-shop parts — a starting reference

A place to begin identifying the components in a glass shop and **where to look for
real part numbers**. This is *not* a spec for any particular shop: read the exact
model/part number off each device's **nameplate** and record that. Manufacturers and
product lines below are common in North-American hot shops; there are others.

| System | Component | Look at (manufacturers / lines) | How to get the real number |
|---|---|---|---|
| **Temperature control** | PID controller | **Novus N480D**, Watlow (PM/EZ-Zone), Fuji (PXR/PXF), Omega (CN) | Model on the controller faceplate |
| | Ramp/soak (kiln/annealer) | **Novus N20K48**, Orton, Bartlett | Faceplate |
| **Power control** | SCR power controller | **Novus MicroFusion**, Watlow (Power Series/DIN-a-mite), Eurotherm | Nameplate: amps, phase, voltage |
| | Mercury displacement relay / contactor | MDI, Durakool, Watlow (legacy) | Coil voltage + amp rating on the body |
| | Breakers / panelboard | Square D, Eaton, Siemens | Panel directory + breaker stamp |
| **Heating elements** | Silicon-carbide (SiC) rods | **I Squared R Element Co (Starbar)**, Kanthal (Globar) | Hot-zone length, overall length, Ø, terminal — measure + match |
| | MoSi₂ elements | Kanthal (Super), I Squared R | Element size code |
| **Sensing** | Thermocouple | K-type (chromel-alumel) or S-type (Pt/Pt-Rh); Omega, Watlow | Type + sheath dia/length + connector |
| **Air / combustion** | Combustion/forced-air blower | Dayton, Fasco, cincinnati fan | Motor nameplate (HP, RPM, V, FLA) |
| | Air compressor | Various | Tank/pump nameplate |
| | Exhaust motor / fan | Dayton, Greenheck | Motor nameplate |
| **Gas train** | Gas pressure regulator | **Maxitrol**, Sensus | Body stamp (inlet/outlet pressure, size) |
| | Safety/solenoid gas valve | Honeywell, ASCO | Valve body label |
| | Manual shutoff / needle / regulators (O₂/propane) | Victor, Harris (for torch gases) | Regulator label |
| **Vessel / lift** | Crucible | AMACO/Sunrise, others (by furnace) | Capacity (lb) + furnace spec |
| | Pneumatic lift / cylinder | Bimba, SMC, etc. | Cylinder stamp |
| **Safety** | Door interlock switch | Honeywell/Omron limit switch | Switch body |
| | CO / gas detector | — | (add to every shop) |

**Sourcing.** Elements from the element maker or a kiln-parts distributor; controls
from an industrial-controls distributor or the maker; gas/electrical parts from a
supply house — and gas/electrical *installation* from a licensed contractor.

Publish your filled-in BOM with your shop (Explore → Add → Shop) so the next person
with the same furnace can find the part in one search.
