"""Debug: does setFlowRate(0) on separator outlet propagate to downstream equipment?

Investigates why a compressor created from a zero-flow separator outlet
ends up with non-zero flow on its own outlet stream.
"""

from jneqsim import neqsim

# --- Setup: create a feed at epsilon flow, run separator ---
feed_thermo = neqsim.thermo.system.SystemSrkEos(293.15, 50.0)
feed_thermo.addComponent("methane", 0.70)
feed_thermo.addComponent("ethane", 0.10)
feed_thermo.addComponent("propane", 0.10)
feed_thermo.addComponent("n-butane", 0.10)
feed_thermo.setMixingRule(2)
feed_thermo.setTotalFlowRate(1.0, "kg/hr")

feed_stream = neqsim.process.equipment.stream.Stream("feed", feed_thermo)
feed_stream.run()

sep = neqsim.process.equipment.separator.Separator("sep", feed_stream)
sep.run()

gas_out = sep.getGasOutStream()
liq_out = sep.getLiquidOutStream()

print("=== After separator run (epsilon flow) ===")
print(f"Gas flow (stream): {gas_out.getFlowRate('kg/hr'):.6e}")
print(f"Liq flow (stream): {liq_out.getFlowRate('kg/hr'):.6e}")
print(f"Gas flow (thermo): {gas_out.getThermoSystem().getFlowRate('kg/hr'):.6e}")

# --- Q1: Does setFlowRate(0) on the stream object work? ---
print("\n=== Q1: setFlowRate(0) on stream object ===")
gas_out.setFlowRate(0.0, "kg/hr")
print(f"Gas stream.getFlowRate: {gas_out.getFlowRate('kg/hr'):.6e}")
print(f"Gas thermo.getFlowRate: {gas_out.getThermoSystem().getFlowRate('kg/hr'):.6e}")

# --- Q2: Does a compressor created from zero-flow stream see zero flow? ---
print("\n=== Q2: Compressor from zero-flow gas outlet ===")
comp = neqsim.process.equipment.compressor.Compressor("comp", gas_out)
comp.setOutletPressure(80.0, "bara")
comp.setIsentropicEfficiency(0.75)
comp_outlet = comp.getOutletStream()
print(f"Comp outlet flow BEFORE run: {comp_outlet.getFlowRate('kg/hr'):.6e}")
print(f"Comp outlet thermo flow:     {comp_outlet.getThermoSystem().getFlowRate('kg/hr'):.6e}")

# --- Q3: What does is_zero_or_ghost_flow see? ---
print("\n=== Q3: Flow check on streams ===")
threshold = 1e-10
for name, s in [("gas_out", gas_out), ("comp_outlet", comp_outlet)]:
    flow = float(s.getFlowRate("kg/hr"))
    below = flow < threshold
    print(f"  {name}: flow={flow:.6e}, below_threshold={below}")

# --- Q4: Alternative approach - set flow on thermo system directly ---
print("\n=== Q4: Try setTotalFlowRate on thermo, then create compressor ===")
gas_out2 = sep.getGasOutStream()  # fresh reference
gas_out2.getThermoSystem().setTotalFlowRate(0.0, "kg/hr")
print(f"Gas thermo flow after setTotalFlowRate(0): {gas_out2.getThermoSystem().getFlowRate('kg/hr'):.6e}")
print(f"Gas stream flow after setTotalFlowRate(0): {gas_out2.getFlowRate('kg/hr'):.6e}")

# --- Q5: What if we re-run the stream after setting zero flow? ---
print("\n=== Q5: Re-run stream after setFlowRate(0) ===")
gas_out3 = sep.getGasOutStream()
gas_out3.setFlowRate(0.0, "kg/hr")
print(f"Before run: stream={gas_out3.getFlowRate('kg/hr'):.6e}")
# DON'T run - it would reset things. Just check what a compressor sees.

# --- Q6: The REAL flow path - what does the compressor inlet see? ---
print("\n=== Q6: Compressor constructor cloning behavior ===")
# Re-create separator fresh
sep2 = neqsim.process.equipment.separator.Separator("sep2", feed_stream)
sep2.run()
gas2 = sep2.getGasOutStream()

# Set zero flow BOTH ways
gas2.setFlowRate(0.0, "kg/hr")
gas2.getThermoSystem().setTotalFlowRate(0.0, "kg/hr")
print(f"Gas2 stream flow: {gas2.getFlowRate('kg/hr'):.6e}")
print(f"Gas2 thermo flow: {gas2.getThermoSystem().getFlowRate('kg/hr'):.6e}")
print(f"Gas2 thermo totalMoles: {gas2.getThermoSystem().getTotalNumberOfMoles():.6e}")

comp2 = neqsim.process.equipment.compressor.Compressor("comp2", gas2)
comp2.setOutletPressure(80.0, "bara")
comp2_out = comp2.getOutletStream()
print(f"Comp2 outlet flow: {comp2_out.getFlowRate('kg/hr'):.6e}")
print(f"Comp2 outlet thermo flow: {comp2_out.getThermoSystem().getFlowRate('kg/hr'):.6e}")
print(f"Comp2 outlet thermo totalMoles: {comp2_out.getThermoSystem().getTotalNumberOfMoles():.6e}")

# --- Q7: Check if the inlet stream used by compressor constructor got cloned with flow ---
print("\n=== Q7: Compressor inlet stream after construction ===")
comp2_in = comp2.getInletStream()
print(f"Comp2 inlet flow: {comp2_in.getFlowRate('kg/hr'):.6e}")
print(f"Comp2 inlet thermo flow: {comp2_in.getThermoSystem().getFlowRate('kg/hr'):.6e}")
