# Valve Implementation Guide for Frontend

## Overview

This document provides a comprehensive guide for implementing valve functionality in the Kappasim frontend. The valve system supports multiple calculation modes, valve characteristics, and flow coefficient types with intelligent validation.

## Core Concepts

### 1. Valve Calculation Modes

The valve supports three distinct calculation modes:

#### **OUTLET_PRESSURE** (Sizing Mode)
- **Purpose**: Calculate required valve size for a specific outlet pressure
- **User Input**: Target outlet pressure (bara)
- **Optional**: Physical valve specification (max_flow_coefficient)
- **Backend Calculates**: Required flow coefficient and/or valve opening percentage

#### **PRESSURE_DROP** (Sizing Mode)
- **Purpose**: Calculate required valve size for a specific pressure drop
- **User Input**: Target pressure drop (bar)
- **Optional**: Physical valve specification (max_flow_coefficient)
- **Backend Calculates**: Required flow coefficient and/or valve opening percentage

#### **CV_OPENING** (Control Mode)
- **Purpose**: Specify a physical valve and its opening percentage
- **User Input**: Maximum flow coefficient, valve opening percentage, flow coefficient type
- **Backend Calculates**: Outlet pressure and pressure drop based on valve performance

### 2. Flow Coefficient System

#### **Flow Coefficient Types**
- **CV**: US units (gal/min per psi) - Default
- **KV**: SI units (L/min per bar)

#### **Flow Coefficient Fields**
- **max_flow_coefficient**: Maximum flow capacity at 100% valve opening
- **effective_flow_coefficient**: Actual flow capacity at current opening (calculated)
- **flow_coefficient_type**: Unit type (CV or KV)

### 3. Valve Characteristics

Controls the relationship between valve opening and flow:

#### **LINEAR** (Default)
- **Formula**: `effective_coefficient = max_coefficient * (opening / 100)`
- **Behavior**: Linear relationship between opening and flow
- **Use Case**: General applications, simple control

#### **EQUAL_PERCENTAGE**
- **Formula**: `effective_coefficient = max_coefficient * R^((opening - 100) / 100)`
- **Parameters**: Requires `rangeability` (R value, typically 20-50)
- **Behavior**: Logarithmic relationship - better control at low flows
- **Use Case**: Wide-range applications, precise control

## Valve Calculation Logic

### How Kappasim Calculates Valve Performance

Kappasim uses a sophisticated approach that separates thermodynamic calculations from valve characteristic implementation:

#### **1. Thermodynamic Calculation (NeqSim)**
- NeqSim calculates the **required effective flow coefficient** based on fluid properties, flow rate, and pressure conditions
- This calculation is independent of valve characteristics and represents the true thermodynamic requirement
- NeqSim uses rigorous fluid dynamics equations to determine what flow coefficient is needed

#### **2. Valve Characteristic Implementation (Kappasim)**
- Kappasim implements valve characteristics using proper mathematical relationships
- For sizing modes (OUTLET_PRESSURE, PRESSURE_DROP), Kappasim calculates the valve opening percentage that would produce the required effective flow coefficient
- For control mode (CV_OPENING), Kappasim calculates the effective flow coefficient from the specified opening percentage

#### **3. Calculation Flow by Mode**

**OUTLET_PRESSURE & PRESSURE_DROP Modes:**
1. NeqSim calculates required effective CV from thermodynamics
2. Kappasim calculates opening % using: `opening = f^(-1)(effective_cv / max_cv)`
   - Linear: `opening = (effective_cv / max_cv) * 100`
   - Equal percentage: `opening = 100 + 100 * ln(effective_cv / max_cv) / ln(R)`

**CV_OPENING Mode:**
1. Kappasim calculates effective CV using: `effective_cv = max_cv * f(opening)`
   - Linear: `effective_cv = max_cv * (opening / 100)`
   - Equal percentage: `effective_cv = max_cv * R^((opening - 100) / 100)`
2. NeqSim calculates outlet pressure from the effective CV

#### **4. Why This Approach is Superior**
- **Thermodynamic accuracy**: Uses rigorous fluid dynamics calculations
- **Characteristic precision**: Implements true valve characteristic mathematics
- **Mode consistency**: Same logic works for all calculation modes
- **Physical realism**: Separates fluid behavior from valve mechanical behavior

### Validation and Warnings

The valve system includes intelligent validation to catch unrealistic scenarios:

#### **Physical Impossibilities**
- Negative outlet pressures (indicates severe undersizing)
- Extreme pressure drops (>500 bar, suggests numerical breakdown)

#### **Sizing Warnings**
- Valve opening >99.5% suggests undersizing for good control range
- Very low opening percentages may indicate oversizing

#### **Best Practices for Users**
- For control applications, aim for 30-80% opening in normal operation
- Equal percentage valves provide better control at low flows
- Linear valves are simpler and adequate for most applications
- Consider rangeability: higher values (30-50) provide wider control range

## Frontend Implementation

### 1. Form Field Structure

```typescript
interface ValveInput {
  // Required: Calculation mode selection
  calculation_mode: 'outlet_pressure' | 'pressure_drop' | 'cv_opening'

  // Mode-specific required fields
  outlet_pressure?: number        // Required for OUTLET_PRESSURE mode
  pressure_drop?: number          // Required for PRESSURE_DROP mode
  max_flow_coefficient?: number   // Required for CV_OPENING mode
  percent_opening?: number        // Required for CV_OPENING mode

  // Flow coefficient configuration
  flow_coefficient_type: 'cv' | 'kv'  // Default: 'cv'

  // Valve characteristic configuration
  valve_characteristic: 'linear' | 'equal_percentage'  // Default: 'linear'
  rangeability?: number  // Required only for equal_percentage, range: 2.0-100.0

  // Optional
  is_isothermal?: boolean  // Default: false
}
```

### 2. Dynamic Form Validation

#### **Field Visibility Logic**
```typescript
const showField = {
  outlet_pressure: calculationMode === 'outlet_pressure',
  pressure_drop: calculationMode === 'pressure_drop',
  max_flow_coefficient: calculationMode === 'cv_opening',
  percent_opening: calculationMode === 'cv_opening',
  rangeability: valveCharacteristic === 'equal_percentage'
}
```

#### **Required Field Validation**
```typescript
const requiredFields = {
  outlet_pressure: calculationMode === 'outlet_pressure',
  pressure_drop: calculationMode === 'pressure_drop',
  max_flow_coefficient: calculationMode === 'cv_opening',
  percent_opening: calculationMode === 'cv_opening',
  rangeability: valveCharacteristic === 'equal_percentage'
}
```

### 3. Form Field Specifications

#### **Calculation Mode Selector**
```typescript
const calculationModeOptions = [
  { value: 'outlet_pressure', label: 'Outlet Pressure', description: 'Size valve for target outlet pressure' },
  { value: 'pressure_drop', label: 'Pressure Drop', description: 'Size valve for target pressure drop' },
  { value: 'cv_opening', label: 'CV Opening', description: 'Specify physical valve and opening' }
]
```

#### **Flow Coefficient Type Selector**
```typescript
const flowCoefficientTypeOptions = [
  { value: 'cv', label: 'CV (US)', description: 'gal/min per psi' },
  { value: 'kv', label: 'Kv (SI)', description: 'L/min per bar' }
]
```

#### **Valve Characteristic Selector**
```typescript
const valveCharacteristicOptions = [
  { value: 'linear', label: 'Linear', description: 'Linear flow vs opening relationship' },
  { value: 'equal_percentage', label: 'Equal Percentage', description: 'Logarithmic flow for wide-range control' }
]
```

#### **Numeric Field Specifications**
```typescript
const fieldSpecs = {
  outlet_pressure: { min: 0.1, max: 1000, step: 0.1, unit: 'bara' },
  pressure_drop: { min: 0.1, max: 500, step: 0.1, unit: 'bar' },
  max_flow_coefficient: { min: 0.1, max: 10000, step: 0.1, unit: 'CV/Kv' },
  percent_opening: { min: 0, max: 100, step: 1, unit: '%' },
  rangeability: { min: 2.0, max: 100.0, step: 1, unit: '' }
}
```

### 4. User Experience Guidelines

#### **Form Organization**
1. **Calculation Mode**: Primary selector at top
2. **Mode-specific inputs**: Show/hide based on mode selection
3. **Flow coefficient config**: Group CV/Kv type with max coefficient field
4. **Valve characteristics**: Group characteristic type with rangeability
5. **Advanced options**: Collapsible section for isothermal toggle

#### **Help Text and Validation Messages**

```typescript
const helpText = {
  calculation_mode: "Choose how you want to specify the valve operation",
  outlet_pressure: "Target pressure after the valve (must be less than inlet)",
  pressure_drop: "Pressure reduction across the valve",
  max_flow_coefficient: "Maximum flow capacity of the physical valve at 100% opening",
  percent_opening: "How much the valve is opened (0% = closed, 100% = fully open)",
  flow_coefficient_type: "Choose CV (US units) or Kv (SI units)",
  valve_characteristic: "Controls how flow changes with valve opening",
  rangeability: "Ratio of max to min controllable flow (typically 20-50)"
}

const validationMessages = {
  outlet_pressure_required: "Outlet pressure is required for this calculation mode",
  pressure_drop_required: "Pressure drop is required for this calculation mode",
  cv_opening_required: "Both max flow coefficient and opening percentage are required",
  rangeability_required: "Rangeability is required for equal percentage valves",
  rangeability_range: "Rangeability must be between 2.0 and 100.0"
}
```

### 5. Results Display

#### **Results Structure**
```typescript
interface ValveResults {
  // Operating conditions
  inlet_pressure: number
  outlet_pressure: number
  pressure_drop: number
  inlet_temperature: number
  outlet_temperature: number

  // Flow coefficient information
  max_flow_coefficient?: number           // null for sizing modes without physical valve
  effective_flow_coefficient?: number     // null if calculation fails
  flow_coefficient_type: 'cv' | 'kv'
  percent_opening?: number                // null for sizing modes without physical valve
  valve_characteristic: 'linear' | 'equal_percentage'
  flow_coefficient_is_specified: boolean  // true if user specified max coefficient

  // Optional properties
  entropy_production?: number
  is_choked?: boolean
}
```

#### **Results Display Logic**
```typescript
const displayResults = (results: ValveResults) => {
  // Always show operating conditions
  const operatingConditions = [
    { label: 'Inlet Pressure', value: results.inlet_pressure, unit: 'bara' },
    { label: 'Outlet Pressure', value: results.outlet_pressure, unit: 'bara' },
    { label: 'Pressure Drop', value: results.pressure_drop, unit: 'bar' }
  ]

  // Flow coefficient display depends on mode
  const flowCoefficientInfo = []

  if (results.flow_coefficient_is_specified) {
    // Physical valve specified - show max, effective, and opening
    flowCoefficientInfo.push(
      { label: `Max ${results.flow_coefficient_type.toUpperCase()}`, value: results.max_flow_coefficient },
      { label: `Effective ${results.flow_coefficient_type.toUpperCase()}`, value: results.effective_flow_coefficient },
      { label: 'Valve Opening', value: results.percent_opening, unit: '%' }
    )
  } else {
    // Sizing mode - show required effective coefficient only
    flowCoefficientInfo.push(
      { label: `Required ${results.flow_coefficient_type.toUpperCase()}`, value: results.effective_flow_coefficient }
    )
  }

  return { operatingConditions, flowCoefficientInfo }
}
```

### 6. Common UI Patterns

#### **Conditional Field Rendering**
```jsx
// Example React component structure
const ValveForm = () => {
  const [calculationMode, setCalculationMode] = useState('outlet_pressure')
  const [valveCharacteristic, setValveCharacteristic] = useState('linear')

  return (
    <form>
      <CalculationModeSelector value={calculationMode} onChange={setCalculationMode} />

      {calculationMode === 'outlet_pressure' && (
        <OutletPressureField required />
      )}

      {calculationMode === 'pressure_drop' && (
        <PressureDropField required />
      )}

      {calculationMode === 'cv_opening' && (
        <>
          <MaxFlowCoefficientField required />
          <PercentOpeningField required />
        </>
      )}

      <FlowCoefficientTypeSelector />
      <ValveCharacteristicSelector value={valveCharacteristic} onChange={setValveCharacteristic} />

      {valveCharacteristic === 'equal_percentage' && (
        <RangeabilityField required />
      )}
    </form>
  )
}
```

### 7. Error Handling

#### **Backend Error Messages**
The backend provides contextual error messages. Common patterns:

- **Mode validation**: "CV_OPENING mode requires: max_flow_coefficient, percent_opening. Specify all required parameters for the chosen valve characteristic."
- **Characteristic validation**: "rangeability parameter is required for EQUAL_PERCENTAGE valve characteristic. Typical values: 20 (moderate control) to 50 (wide range control)."

#### **Frontend Error Display**
```typescript
const handleApiError = (error: ApiError) => {
  // Backend provides detailed validation errors
  if (error.type === 'validation_error') {
    // Show field-specific errors near relevant inputs
    return error.details.map(detail => ({
      field: detail.field,
      message: detail.message
    }))
  }

  // Generic error handling
  return [{ field: 'general', message: 'Please check your inputs and try again' }]
}
```

### 8. Best Practices

#### **Progressive Disclosure**
- Start with calculation mode selection
- Show mode-specific fields only when relevant
- Use collapsible sections for advanced options

#### **Sensible Defaults**
- calculation_mode: 'outlet_pressure' (most common)
- flow_coefficient_type: 'cv' (industry standard)
- valve_characteristic: 'linear' (simplest)

#### **Validation Strategy**
- Client-side: Immediate feedback for basic validation (required fields, ranges)
- Server-side: Comprehensive validation with contextual error messages
- Real-time: Update field visibility and requirements as user changes selections

#### **Performance Considerations**
- Debounce API calls for real-time validation
- Cache calculation results for repeated requests
- Use optimistic updates for better responsiveness

## Example Implementation Flow

1. **User selects calculation mode** → Frontend shows/hides relevant fields
2. **User fills required fields** → Client-side validation provides immediate feedback
3. **User selects valve characteristic** → Frontend shows/hides rangeability field
4. **User submits form** → Backend validates all requirements and returns results
5. **Frontend displays results** → Show operating conditions and flow coefficient information
6. **Error handling** → Display contextual error messages near relevant fields

This comprehensive valve implementation provides users with powerful fluid control simulation capabilities while maintaining an intuitive, validated user experience.