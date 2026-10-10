These are the names the installed `oofempy` actually exports. **Use only names from this list.** A name that is not here does not exist in this build, and calling it is an immediate AttributeError.

Keyword arguments are not listed -- read the worked example below for the calling convention, and note that some materials require keywords the example does not show (`isoLE` needs `tAlpha`).

### Analysis types (engineering models)

- `linearStatic` -- one linear solve
- `nonlinearstatic` -- incremental, path-following
- `staticStructural` -- quasi-static structural, Newton-Raphson
- `transientTransport` -- transient heat / mass transport
- `dummyProblem`

### Problem setup

- `domain`
- `domainType` -- enum: _2dPlaneStressMode, _1dTrussMode, ...
- `problemMode`
- `createSet` -- group elements/nodes for assignment
- `node`
- `hangingnode`
- `init`
- `getClassFactory`
- `hasModule`
- `test`

### Cross sections

- `simpleCS` -- structural; takes thick=, material=, set=
- `simpleTransportCS` -- transport counterpart
- `rebarCS`

### Materials - solid mechanics

- `isoLE` -- isotropic linear elastic. REQUIRES d, E, n AND tAlpha
- `concreteDPM` -- damage-plastic concrete
- `concreteFcm` -- fixed crack model
- `concreteFcmViscoelastic` -- fixed crack + viscoelasticity
- `idm1` -- isotropic damage
- `mazarsmodel` -- Mazars damage
- `misesmat` -- von Mises plasticity
- `j2mat` -- J2 plasticity
- `steel1`
- `steelrelaxmat` -- steel relaxation
- `mps` -- MPS creep (B3 family)
- `ec2creepmat` -- Eurocode 2 creep
- `bondceb`
- `rebar3d`

### Materials - transport

- `isoHeat` -- isotropic heat conduction; k, c, d

### Boundary and initial conditions, loads

- `boundaryCondition` -- prescribed value (Dirichlet)
- `initialCondition`
- `nodalLoad`
- `constantEdgeLoad`
- `constantSurfaceLoad`
- `structEigenstrainLoad`
- `structTemperatureLoad` -- imposed temperature as structural load

### Time functions

- `constantFunction`
- `peakFunction`
- `piecewiseLinFunction` -- t=(...), f_t=(...)

### Export modules

- `vtkxml` -- VTU files on disk
- `vtkmemory` -- in-memory export; pythonExport=1 to read values back
- `homExport` -- homogenised averages

### Elements - 2D structural

- `planestress2d`
- `trPlaneStress2d` -- linear triangle, plane stress
- `trplanestress2d`
- `qplanestress2d`
- `qtrplstr`
- `quad1planestrain`
- `trplanestrain`
- `trplanestressrotallman`
- `trplanestressrotallman3d`
- `trplanestrrot`
- `linquad3dplanestress`
- `l4axisymm` -- axisymmetric quad
- `q4axisymm`
- `axisymm3d`

### Elements - 3D structural

- `lspace` -- linear brick
- `lspacebb`
- `ltrspace` -- linear tetrahedron
- `qspace`
- `qtrspace`
- `lwedge`
- `qwedge`

### Elements - beams, trusses, lattice

- `beam2d`
- `beam3d`
- `libeam3d`
- `truss1d`
- `truss2d`
- `truss3d`
- `bondlink3d`
- `lattice2d`
- `lattice3d`
- `lattice2dboundary`
- `lattice3dboundary`
- `latticelink3d`
- `latticelink3dboundary`

### Elements - transport

- `tr1ht`
- `quad1ht`
- `qquad1ht`
- `brick1ht`
- `qbrick1ht`
- `tetrah1ht`
- `line1ht`
- `traxisym1ht`
- `quadAxiSym1ht`
- `quadaxisym1ht`
- `tr1mt`
- `quad1mt`
- `qquad1mt`
- `line1mt`
- `qbrick1mt`
- `tr1hmt`
- `quad1hmt`
- `qquad1hmt`
- `brick1hmt`
- `qbrick1hmt`
- `line1hmt`

### Classes, exceptions and enums

Enum members are listed where the binding exposes them. Names not in this list do not exist.

- `BadFormatInputException`
- `BoundaryCondition`: `BoundaryCondition.setPrescribedValue`
- `CharType`: `CharType.UnknownCharType`, `CharType.StiffnessMatrix`, `CharType.TangentStiffnessMatrix`, `CharType.SecantStiffnessMatrix`, `CharType.ElasticStiffnessMatrix`, `CharType.MassMatrix`, `CharType.LumpedMassMatrix`, `CharType.ConductivityMatrix`, `CharType.CapacityMatrix`, `CharType.InitialStressMatrix`, `CharType.ExternalForcesVector`, `CharType.InternalForcesVector`, `CharType.LastEquilibratedInternalForcesVector`, `CharType.InertiaForcesVector`, `CharType.AuxVelocityLhs`, `CharType.VelocityLhs`, `CharType.PressureGradientMatrix`, `CharType.DivergenceMatrix`, `CharType.VelocityLaplacianMatrix`, `CharType.PressureLaplacianMatrix`, `CharType.StabilizationMassMatrix`, `CharType.PressureGradientVector`, `CharType.MassVelocityVector`, `CharType.MassAuxVelocityVector`, `CharType.LaplacePressureVector`, `CharType.LaplaceVelocityVector`, `CharType.DivergenceAuxVelocityVector`, `CharType.DivergenceVelocityVector`
- `ClassFactory`: `ClassFactory.createElement`, `ClassFactory.createEngngModel`, `ClassFactory.getRegisteredNames`
- `ConvergedReason`: `ConvergedReason.CR_UNKNOWN`, `ConvergedReason.CR_CONVERGED`, `ConvergedReason.CR_DIVERGED_ITS`, `ConvergedReason.CR_DIVERGED_TOL`, `ConvergedReason.CR_FAILED`
- `CrossSection`: `CrossSection.giveMaterial`
- `DataReader`
- `DeadWeight`
- `Dof`: `Dof.giveDofManNumber`, `Dof.giveDofManager`, `Dof.giveDofManGlobalNumber`, `Dof.giveUnknown`, `Dof.giveDofID`, `Dof.printYourself`, `Dof.giveEquationNumber`
- `DofIDItem`: `DofIDItem.Undef`, `DofIDItem.D_u`, `DofIDItem.D_v`, `DofIDItem.D_w`, `DofIDItem.R_u`, `DofIDItem.R_v`, `DofIDItem.R_w`, `DofIDItem.V_u`, `DofIDItem.V_v`, `DofIDItem.V_w`, `DofIDItem.T_f`, `DofIDItem.P_f`, `DofIDItem.G_0`, `DofIDItem.G_1`, `DofIDItem.C_1`, `DofIDItem.W_u`, `DofIDItem.W_v`, `DofIDItem.W_w`, `DofIDItem.Gamma`, `DofIDItem.D_u_edge_const`, `DofIDItem.D_u_edge_lin`, `DofIDItem.D_v_edge_const`, `DofIDItem.D_v_edge_lin`, `DofIDItem.Warp_PsiTheta`, `DofIDItem.Warp_Theta`, `DofIDItem.LMP_u`, `DofIDItem.LMP_v`, `DofIDItem.LMP_w`, `DofIDItem.Trac_u`, `DofIDItem.Trac_v`, `DofIDItem.Trac_w`
- `DofManValueField`: `DofManValueField.addNode`, `DofManValueField.addElement`, `DofManValueField.setDofManValue`, `DofManValueField.getNodeCoordinates`
- `DofManager`: `DofManager.giveDofWithID`, `DofManager.giveNumberOfDofs`, `DofManager.giveUnknownVector`, `DofManager.givePrescribedUnknownVector`, `DofManager.giveCoordinates`, `DofManager.appendDof`, `DofManager.removeDof`, `DofManager.hasDofID`, `DofManager.giveGlobalNumber`
- `Domain`: `Domain.giveNumber`, `Domain.setNumber`, `Domain.giveDofManager`, `Domain.giveElement`, `Domain.giveGlobalDofManager`, `Domain.giveNumberOfElements`, `Domain.giveNumberOfDofManagers`, `Domain.giveNumberOfBoundaryConditions`, `Domain.giveNumberOfFunctions`, `Domain.giveNumberOfMaterialModels`, `Domain.giveNumberOfCrossSectionModels`, `Domain.giveNumberOfInitialConditions`, `Domain.giveNumberOfRegions`, `Domain.giveNumberOfNonlocalBarriers`, `Domain.giveNumberOfSets`, `Domain.giveBc`, `Domain.giveIc`, `Domain.giveFunction`, `Domain.giveMaterial`, `Domain.giveCrossSection`, `Domain.resizeDofManagers`, `Domain.setDofManager`, `Domain.resizeElements`, `Domain.setElement`, `Domain.resizeMaterials`, `Domain.setMaterial`, `Domain.resizeCrossSectionModels`, `Domain.setCrossSection`, `Domain.resizeBoundaryConditions`, `Domain.setBoundaryCondition`, `Domain.resizeInitialConditions`, `Domain.setInitialCondition`, `Domain.resizeFunctions`, `Domain.setFunction`, `Domain.resizeSets`, `Domain.setSet`, `Domain.giveSet`, `Domain.giveSpatialLocalizer`, `Domain.initializeFinish`
- `DynamicInputRecord`: `DynamicInputRecord.finish`, `DynamicInputRecord.setRecordKeywordField`, `DynamicInputRecord.setRecordKeywordNumber`, `DynamicInputRecord.setField`
- `EMTT_AnalysisTimer`
- `EMTT_DataTransferTimer`
- `EMTT_LastTimer`
- `EMTT_LoadBalancingTimer`
- `EMTT_NetComputationalStepTimer`
- `EMTT_SolutionStepTimer`
- `EModelDefaultEquationNumbering`
- `Element`: `Element.giveLocationArray`, `Element.giveCharacteristicMatrix`, `Element.giveCharacteristicVector`, `Element.computeVectorOf`, `Element.computeVectorOfPrescribed`, `Element.giveDofManagerNumber`, `Element.giveDofManArray`, `Element.giveDofManager`, `Element.giveMaterial`, `Element.giveCrossSection`, `Element.giveMaterialNumber`, `Element.giveNumber`, `Element.giveNumberOfDofManagers`, `Element.giveNumberOfNodes`, `Element.giveRegionNumber`, `Element.giveNode`, `Element.updateYourself`, `Element.isActivated`, `Element.isCast`, `Element.giveGeometryType`, `Element.giveIntegrationRule`, `Element.giveDefaultIntegrationRulePtr`, `Element.giveIPValue`, `Element.giveLabel`, `Element.initializeFrom`, `Element.postInitialize`, `Element.setDofManagers`, `Element.setNumberOfDofManagers`, `Element.giveDofManDofIDMask`, `Element.getActivityTimeFunctionNumber`, `Element.setActivityTimeFunctionNumber`
- `Element_Geometry_Type`: `Element_Geometry_Type.EGT_point`, `Element_Geometry_Type.EGT_line_1`, `Element_Geometry_Type.EGT_line_2`, `Element_Geometry_Type.EGT_triangle_1`, `Element_Geometry_Type.EGT_triangle_2`, `Element_Geometry_Type.EGT_quad_1`, `Element_Geometry_Type.EGT_quad_1_interface`, `Element_Geometry_Type.EGT_quad_21_interface`, `Element_Geometry_Type.EGT_quad_2`, `Element_Geometry_Type.EGT_quad9_2`, `Element_Geometry_Type.EGT_tetra_1`, `Element_Geometry_Type.EGT_tetra_2`, `Element_Geometry_Type.EGT_hexa_1`, `Element_Geometry_Type.EGT_hexa_2`, `Element_Geometry_Type.EGT_hexa_27`, `Element_Geometry_Type.EGT_wedge_1`, `Element_Geometry_Type.EGT_wedge_2`, `Element_Geometry_Type.EGT_Composite`, `Element_Geometry_Type.EGT_unknown`
- `EngngModel`: `EngngModel.giveDomain`, `EngngModel.setDomain`, `EngngModel.giveNumberOfDomains`, `EngngModel.setNumberOfDomains`, `EngngModel.giveMetaStep`, `EngngModel.terminateAnalysis`, `EngngModel.terminate`, `EngngModel.solveYourself`, `EngngModel.solveYourselfAt`, `EngngModel.giveField`, `EngngModel.giveLoadLevel`, `EngngModel.giveCurrentStep`, `EngngModel.givePreviousStep`, `EngngModel.giveNextStep`, `EngngModel.giveNumberOfSteps`, `EngngModel.giveUnknownComponent`, `EngngModel.checkProblemConsistency`, `EngngModel.giveTimer`, `EngngModel.init`, `EngngModel.initializeYourself`, `EngngModel.initMetaStepAttributes`, `EngngModel.preInitializeNextStep`, `EngngModel.updateYourself`, `EngngModel.postInitialize`, `EngngModel.setRenumberFlag`, `EngngModel.giveContext`, `EngngModel.forceEquationNumbering`, `EngngModel.giveNumberOfDomainEquations`, `EngngModel.Instanciate_init`, `EngngModel.ndomains`
- `EngngModelContext`: `EngngModelContext.giveFieldManager`
- `EngngModelTimer`: `EngngModelTimer.startTimer`, `EngngModelTimer.stopTimer`, `EngngModelTimer.getUtime`
- `EngngModelTimerType`: `EngngModelTimerType.EMTT_AnalysisTimer`, `EngngModelTimerType.EMTT_SolutionStepTimer`, `EngngModelTimerType.EMTT_NetComputationalStepTimer`, `EngngModelTimerType.EMTT_LoadBalancingTimer`, `EngngModelTimerType.EMTT_DataTransferTimer`, `EngngModelTimerType.EMTT_LastTimer`
- `ExportModule`: `ExportModule.initialize`, `ExportModule.doOutput`, `ExportModule.terminate`
- `ExportRegion`: `ExportRegion.giveNumberOfNodes`, `ExportRegion.giveNodeCoords`, `ExportRegion.giveNumberOfCells`, `ExportRegion.giveCellConnectivity`, `ExportRegion.giveCellType`, `ExportRegion.giveCellOffset`, `ExportRegion.givePrimaryVarInNode`, `ExportRegion.giveInternalVarInNode`, `ExportRegion.giveCellVar`, `ExportRegion.getVertices`, `ExportRegion.getCellConnectivity`, `ExportRegion.getCellTypes`, `ExportRegion.getPrimaryVertexValues`, `ExportRegion.getInternalVertexValues`, `ExportRegion.getCellValues`
- `FEMComponent`: `FEMComponent.giveClassName`, `FEMComponent.giveInputRecordName`, `FEMComponent.giveDomain`, `FEMComponent.setDomain`, `FEMComponent.giveNumber`, `FEMComponent.setNumber`, `FEMComponent.checkConsistency`, `FEMComponent.printYourself`, `FEMComponent.number`
- `Field`: `Field.evaluateAt`, `Field.giveType`, `Field.setType`, `Field.setSetsNumbers`, `Field.hasElementInSets`
- `FieldManager`: `FieldManager.registerField`
- `FieldType`: `FieldType.FT_Unknown`, `FieldType.FT_Velocity`, `FieldType.FT_Displacements`, `FieldType.FT_VelocityPressure`, `FieldType.FT_Pressure`, `FieldType.FT_Temperature`, `FieldType.FT_HumidityConcentration`, `FieldType.FT_TransportProblemUnknowns`, `FieldType.FT_TemperatureAmbient`, `FieldType.FT_EigenStrain`, `FieldType.FT_VOF`, `FieldType.FT_CorrosionMassLoss`, `FieldType.FT_CorrosionFraction`
- `FloatArray`: `FloatArray.printYourself`, `FloatArray.pY`, `FloatArray.resize`, `FloatArray.assemble`, `FloatArray.distance`, `FloatArray.normalize`, `FloatArray.normalize_giveNorm`, `FloatArray.computeNorm`, `FloatArray.product`, `FloatArray.zero`, `FloatArray.beProductOf`, `FloatArray.asNumpyArray`
- `FloatMatrix`: `FloatMatrix.printYourself`, `FloatMatrix.pY`, `FloatMatrix.resize`, `FloatMatrix.isSquare`, `FloatMatrix.assemble`, `FloatMatrix.computeFrobeniusNorm`, `FloatMatrix.computeNorm`, `FloatMatrix.beDiagonal`, `FloatMatrix.giveTrace`, `FloatMatrix.giveDeterminant`, `FloatMatrix.zero`, `FloatMatrix.beUnitMatrix`, `FloatMatrix.beTranspositionOf`, `FloatMatrix.beProductOf`, `FloatMatrix.addProductOf`, `FloatMatrix.addTProductOf`, `FloatMatrix.beTProductOf`, `FloatMatrix.beProductTOf`, `FloatMatrix.beDyadicProductOf`, `FloatMatrix.beInverseOf`, `FloatMatrix.solveForRhs`, `FloatMatrix.plusProductSymmUpper`, `FloatMatrix.plusDyadSymmUpper`, `FloatMatrix.plusProductUnsym`, `FloatMatrix.plusDyadUnsym`, `FloatMatrix.asNumpyArray`
- `Function`: `Function.evaluate`, `Function.evaluateAtTime`, `Function.evaluateVelocityAtTime`, `Function.evaluateAccelerationAtTime`
- `GaussPoint`: `GaussPoint.giveMaterialStatus`, `GaussPoint.hasMaterialStatus`, `GaussPoint.setMaterialStatus`, `GaussPoint.giveElement`, `GaussPoint.giveMaterial`, `GaussPoint.giveGlobalCoordinates`
- `GeneralBoundaryCondition`: `GeneralBoundaryCondition.getIsImposedTimeFunctionNumber`, `GeneralBoundaryCondition.setIsImposedTimeFunctionNumber`
- `HOMExportModule`
- `InitialCondition`: `InitialCondition.giveSetNumber`
- `InputException`
- `InputRecord`
- `InstanciateProblem`
- `IntArray`: `IntArray.resize`, `IntArray.clear`, `IntArray.preallocate`, `IntArray.followedBy`, `IntArray.isEmpty`, `IntArray.containsOnlyZeroes`, `IntArray.containsSorted`, `IntArray.insertSorted`, `IntArray.eraseSorted`, `IntArray.findFirstIndexOf`, `IntArray.contains`, `IntArray.sort`, `IntArray.zero`, `IntArray.pY`
- `IntELPoint`
- `IntegrationPointStatus`: `IntegrationPointStatus.giveClassName`
- `IntegrationPointStatusIDType`: `IntegrationPointStatusIDType.IPSID_Default`, `IntegrationPointStatusIDType.PIDControllerStatusID`
- `IntegrationRule`: `IntegrationRule.giveNumberOfIntegrationPoints`, `IntegrationRule.getIntegrationPoint`
- `InterfaceCS`
- `InternalStateType`: `InternalStateType.IST_Undefined`, `InternalStateType.IST_StressTensor`, `InternalStateType.IST_PrincipalStressTensor`, `InternalStateType.IST_PrincipalStressTempTensor`, `InternalStateType.IST_StrainTensor`, `InternalStateType.IST_PrincipalStrainTensor`, `InternalStateType.IST_PrincipalStrainTempTensor`, `InternalStateType.IST_BeamForceMomentTensor`, `InternalStateType.IST_BeamStrainCurvatureTensor`, `InternalStateType.IST_ShellMomentTensor`, `InternalStateType.IST_ShellForceTensor`, `InternalStateType.IST_CurvatureTensor`, `InternalStateType.IST_DisplacementVector`, `InternalStateType.IST_DamageTensor`, `InternalStateType.IST_DamageInvTensor`, `InternalStateType.IST_PrincipalDamageTensor`, `InternalStateType.IST_PrincipalDamageTempTensor`, `InternalStateType.IST_CrackState`, `InternalStateType.IST_StressTensorTemp`, `InternalStateType.IST_StrainTensorTemp`, `InternalStateType.IST_ShellForceTensorTemp`, `InternalStateType.IST_ShellMomentTensorTemp`, `InternalStateType.IST_CurvatureTensorTemp`, `InternalStateType.IST_DisplacementVectorTemp`, `InternalStateType.IST_DamageTensorTemp`, `InternalStateType.IST_DamageInvTensorTemp`, `InternalStateType.IST_CrackStateTemp`, `InternalStateType.IST_PlasticStrainTensor`, `InternalStateType.IST_PrincipalPlasticStrainTensor`, `InternalStateType.IST_CylindricalStressTensor`, `InternalStateType.IST_CylindricalStrainTensor`, `InternalStateType.IST_MaxEquivalentStrainLevel`, `InternalStateType.IST_ErrorIndicatorLevel`, `InternalStateType.IST_InternalStressError`, `InternalStateType.IST_PrimaryUnknownError`, `InternalStateType.IST_RelMeshDensity`, `InternalStateType.IST_MicroplaneDamageValues`, `InternalStateType.IST_Temperature`, `InternalStateType.IST_MassConcentration_1`, `InternalStateType.IST_HydrationDegree` ... and 97 more
- `InternalVariableField`
- `LSpace`: `LSpace.computeVolumeAround`
- `MatResponseMode`: `MatResponseMode.TangentStiffness`, `MatResponseMode.SecantStiffness`, `MatResponseMode.ElasticStiffness`, `MatResponseMode.Conductivity`, `MatResponseMode.Conductivity_ww`, `MatResponseMode.Conductivity_hh`, `MatResponseMode.Conductivity_hw`, `MatResponseMode.Conductivity_wh`, `MatResponseMode.Capacity`, `MatResponseMode.Capacity_ww`, `MatResponseMode.Capacity_hh`, `MatResponseMode.Capacity_hw`, `MatResponseMode.Capacity_wh`, `MatResponseMode.IntSource`, `MatResponseMode.IntSource_ww`, `MatResponseMode.IntSource_hh`, `MatResponseMode.IntSource_hw`, `MatResponseMode.IntSource_wh`
- `Material`: `Material.giveStatus`, `Material.CreateStatus`, `Material.giveIPValue`, `Material.initializeFrom`
- `MaterialMappingAlgorithmType`: `MaterialMappingAlgorithmType.MMA_ClosestPoint`, `MaterialMappingAlgorithmType.MMA_LeastSquareProjection`, `MaterialMappingAlgorithmType.MMA_ShapeFunctionProjection`
- `MaterialMode`
- `MaterialStatus`
- `MetaStep`: `MetaStep.setNumberOfSteps`, `MetaStep.giveNumberOfSteps`
- `MissingKeywordInputException`
- `OOFEMTXTDataReader`
- `OOFEMTXTInputRecord`: `OOFEMTXTInputRecord.finish`, `OOFEMTXTInputRecord.setRecordString`
- `PythonField`: `PythonField.setModuleName`, `PythonField.setFunctionName`
- `Rebar3d`: `Rebar3d.computeMassCorroded`
- `Set`: `Set.setElementList`, `Set.setBoundaryList`, `Set.setNodeList`, `Set.setEdgeList`
- `SparseLinearSystemNM`: `SparseLinearSystemNM.solve`
- `SparseMtrx`: `SparseMtrx.giveNumberOfRows`, `SparseMtrx.giveNumberOfColumns`, `SparseMtrx.times`, `SparseMtrx.timesT`, `SparseMtrx.add`, `SparseMtrx.addDiagonal`, `SparseMtrx.buildInternalStructure`, `SparseMtrx.assemble`, `SparseMtrx.at`, `SparseMtrx.printYourself`
- `SpatialLocalizer`: `SpatialLocalizer.giveClosestIP`
- `StaggeredProblem`: `StaggeredProblem.giveSlaveProblem`, `StaggeredProblem.giveTimeControl`
- `StructuralElement`: `StructuralElement.giveDofManDofIDMask`
- `StructuralMaterial`: `StructuralMaterial.giveStatus`, `StructuralMaterial.CreateStatus`
- `StructuralMaterialStatus`: `StructuralMaterialStatus.giveStrainVector`, `StructuralMaterialStatus.giveStressVector`, `StructuralMaterialStatus.letTempStressVectorBe`, `StructuralMaterialStatus.letTempStrainVectorBe`
- `TimeDiscretizationType`: `TimeDiscretizationType.TD_Unspecified`, `TimeDiscretizationType.TD_ThreePointBackward`, `TimeDiscretizationType.TD_TwoPointBackward`, `TimeDiscretizationType.TD_Newmark`, `TimeDiscretizationType.TD_Wilson`, `TimeDiscretizationType.TD_Explicit`
- `TimeStep`: `TimeStep.giveNumber`, `TimeStep.giveTargetTime`, `TimeStep.giveIntrinsicTime`, `TimeStep.giveTimeIncrement`, `TimeStep.setTimeIncrement`, `TimeStep.setTime`, `TimeStep.setTargetTime`, `TimeStep.setIntrinsicTime`, `TimeStep.isNotTheLastStep`, `TimeStep.isTheFirstStep`, `TimeStep.isTheCurrentTimeStep`
- `UniformGridField`: `UniformGridField.setGeometry`, `UniformGridField.setValues`, `UniformGridField.nodeValue2d`, `UniformGridField.nodeValue3d`
- `UnknownNumberingScheme`: `UnknownNumberingScheme.init`, `UnknownNumberingScheme.giveDofEquationNumber`
- `UnknownType`: `UnknownType.DisplacementVector`, `UnknownType.Temperature`
- `UnstructuredGridField`: `UnstructuredGridField.addVertex`, `UnstructuredGridField.setVertexValue`, `UnstructuredGridField.addCell`
- `VTKBaseExportModule`
- `VTKHDF5Reader`: `VTKHDF5Reader.initialize`, `VTKHDF5Reader.finalize`, `VTKHDF5Reader.readMesh`, `VTKHDF5Reader.readField`
- `VTKMemoryExportModule`: `VTKMemoryExportModule.getExportRegions`
- `VTKXMLExportModule`: `VTKXMLExportModule.getExportRegions`
- `ValueInputException`
- `ValueModeType`: `ValueModeType.VM_Unknown`, `ValueModeType.VM_Total`, `ValueModeType.VM_Velocity`, `ValueModeType.VM_Acceleration`, `ValueModeType.VM_Incremental`, `ValueModeType.VM_RhsTotal`, `ValueModeType.VM_RhsIncremental`, `ValueModeType.VM_RhsInitial`, `ValueModeType.VM_Intermediate`, `ValueModeType.VM_TotalIntrinsic`

Element name suffixes are the transport convention, and they are load-bearing:

    ...ht    heat transport
    ...mt    mass (moisture) transport
    ...hmt   coupled heat AND mass transport

so `quad1ht` is the heat-transport quadrilateral and `quad1hmt` its coupled
counterpart. Pick the element family that matches the fields you are solving.
