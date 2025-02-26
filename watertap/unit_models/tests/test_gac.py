#################################################################################
# WaterTAP Copyright (c) 2020-2023, The Regents of the University of California,
# through Lawrence Berkeley National Laboratory, Oak Ridge National Laboratory,
# National Renewable Energy Laboratory, and National Energy Technology
# Laboratory (subject to receipt of any required approvals from the U.S. Dept.
# of Energy). All rights reserved.
#
# Please see the files COPYRIGHT.md and LICENSE.md for full copyright and license
# information, respectively. These files are also available online at the URL
# "https://github.com/watertap-org/watertap/"
#################################################################################

import pytest
import pyomo.environ as pyo
from pyomo.environ import (
    ConcreteModel,
    check_optimal_termination,
    value,
)
from pyomo.network import Port
from pyomo.util.check_units import assert_units_consistent

from idaes.core import (
    FlowsheetBlock,
    EnergyBalanceType,
    MaterialBalanceType,
    MomentumBalanceType,
)
from idaes.core.solvers import get_solver
from idaes.core.util.model_statistics import (
    degrees_of_freedom,
    number_variables,
    number_total_constraints,
    number_unused_variables,
)
from idaes.core.util.testing import initialization_tester
from idaes.core.util.scaling import (
    calculate_scaling_factors,
    unscaled_variables_generator,
    badly_scaled_var_generator,
)
from idaes.core.util.exceptions import ConfigurationError
from idaes.core import UnitModelCostingBlock

from watertap.property_models.multicomp_aq_sol_prop_pack import (
    MCASParameterBlock,
    DiffusivityCalculation,
)
from watertap.unit_models.gac import (
    GAC,
    FilmTransferCoefficientType,
    SurfaceDiffusionCoefficientType,
)
from watertap.costing import WaterTAPCosting

__author__ = "Hunter Barber"

solver = get_solver()

# inputs for badly_scaled_var_generator used across test frames
sv_large = 1e2
sv_small = 1e-2
sv_zero = 1e-8

# -----------------------------------------------------------------------------
class TestGACSimplified:
    @pytest.fixture(scope="class")
    def gac_frame_simplified(self):
        ms = ConcreteModel()
        ms.fs = FlowsheetBlock(dynamic=False)

        ms.fs.properties = MCASParameterBlock(
            solute_list=["DCE"],
            mw_data={"H2O": 0.018, "DCE": 0.09896},
        )
        ms.fs.unit = GAC(
            property_package=ms.fs.properties,
            film_transfer_coefficient_type="fixed",
            surface_diffusion_coefficient_type="fixed",
        )

        # feed specifications
        ms.fs.unit.process_flow.properties_in[0].pressure.fix(
            101325
        )  # feed pressure [Pa]
        ms.fs.unit.process_flow.properties_in[0].temperature.fix(
            273.15 + 25
        )  # feed temperature [K]
        ms.fs.unit.process_flow.properties_in[0].flow_mol_phase_comp["Liq", "H2O"].fix(
            55555.55426666667
        )
        ms.fs.unit.process_flow.properties_in[0].flow_mol_phase_comp["Liq", "DCE"].fix(
            0.0002344381568310428
        )

        # trial problem from Hand, 1984 for removal of trace DCE
        # adsorption isotherm
        ms.fs.unit.freund_k.fix(37.9e-6 * (1e6**0.8316))
        ms.fs.unit.freund_ninv.fix(0.8316)
        # gac particle specifications
        ms.fs.unit.particle_dens_app.fix(722)
        ms.fs.unit.particle_dia.fix(0.00106)
        # adsorber bed specifications
        ms.fs.unit.ebct.fix(300)  # seconds
        ms.fs.unit.bed_voidage.fix(0.449)
        ms.fs.unit.bed_length.fix(6)  # assumed
        # design spec
        ms.fs.unit.conc_ratio_replace.fix(0.50)
        # parameters
        ms.fs.unit.kf.fix(3.29e-5)
        ms.fs.unit.ds.fix(1.77e-13)
        ms.fs.unit.a0.fix(3.68421)
        ms.fs.unit.a1.fix(13.1579)
        ms.fs.unit.b0.fix(0.784576)
        ms.fs.unit.b1.fix(0.239663)
        ms.fs.unit.b2.fix(0.484422)
        ms.fs.unit.b3.fix(0.003206)
        ms.fs.unit.b4.fix(0.134987)

        return ms

    @pytest.mark.unit
    def test_simplified_config(self, gac_frame_simplified):
        ms = gac_frame_simplified
        # check unit config arguments
        assert len(ms.fs.unit.config) == 12

        assert not ms.fs.unit.config.dynamic
        assert not ms.fs.unit.config.has_holdup
        assert ms.fs.unit.config.material_balance_type == MaterialBalanceType.useDefault
        assert ms.fs.unit.config.energy_balance_type == EnergyBalanceType.none
        assert (
            ms.fs.unit.config.momentum_balance_type == MomentumBalanceType.pressureTotal
        )
        assert (
            ms.fs.unit.config.film_transfer_coefficient_type
            == FilmTransferCoefficientType.fixed
        )
        assert (
            ms.fs.unit.config.surface_diffusion_coefficient_type
            == SurfaceDiffusionCoefficientType.fixed
        )
        assert ms.fs.unit.config.finite_elements_ss_approximation == 5

        # check properties
        assert ms.fs.unit.config.property_package is ms.fs.properties
        assert ms.fs.unit.config.property_package is ms.fs.properties
        assert len(ms.fs.unit.config.property_package.solute_set) == 1
        assert len(ms.fs.unit.config.property_package.solvent_set) == 1
        assert ms.fs.properties.config.diffus_calculation == DiffusivityCalculation.none

    @pytest.mark.unit
    def test_simplified_build(self, gac_frame_simplified):
        ms = gac_frame_simplified

        # test units
        assert assert_units_consistent(ms) is None

        # test ports
        port_lst = ["inlet", "outlet", "adsorbed"]
        for port_str in port_lst:
            port = getattr(ms.fs.unit, port_str)
            assert len(port.vars) == 3  # number of state variables for property package
            assert isinstance(port, Port)

        # test statistics
        assert number_variables(ms) == 100
        assert number_total_constraints(ms) == 64
        assert number_unused_variables(ms) == 11  # dens parameters from properties

    @pytest.mark.unit
    def test_simplified_dof(self, gac_frame_simplified):
        ms = gac_frame_simplified
        assert degrees_of_freedom(ms) == 0

    @pytest.mark.unit
    def test_simplified_calculate_scaling(self, gac_frame_simplified):
        ms = gac_frame_simplified

        ms.fs.properties.set_default_scaling(
            "flow_mol_phase_comp", 1e-4, index=("Liq", "H2O")
        )
        ms.fs.properties.set_default_scaling(
            "flow_mol_phase_comp", 1e4, index=("Liq", "DCE")
        )
        calculate_scaling_factors(ms)

        # check that all variables have scaling factors
        unscaled_var_list = list(unscaled_variables_generator(ms))
        assert len(unscaled_var_list) == 0

    @pytest.mark.component
    def test_simplified_initialize(self, gac_frame_simplified):
        initialization_tester(gac_frame_simplified)

    @pytest.mark.component
    def test_simplified_var_scaling_init(self, gac_frame_simplified):
        ms = gac_frame_simplified
        badly_scaled_var_lst = list(
            badly_scaled_var_generator(ms, large=sv_large, small=sv_small, zero=sv_zero)
        )
        print([(x[0].name, x[1]) for x in badly_scaled_var_lst])
        assert badly_scaled_var_lst == []

    @pytest.mark.component
    def test_simplified_solve(self, gac_frame_simplified):
        ms = gac_frame_simplified
        results = solver.solve(ms)

        # Check for optimal solution
        assert check_optimal_termination(results)

    @pytest.mark.component
    def test_simplified_var_scaling_solve(self, gac_frame_simplified):
        ms = gac_frame_simplified
        badly_scaled_var_lst = list(
            badly_scaled_var_generator(ms, large=sv_large, small=sv_small, zero=sv_zero)
        )
        assert badly_scaled_var_lst == []

    @pytest.mark.component
    def test_simplified_solution(self, gac_frame_simplified):
        ms = gac_frame_simplified

        # Approx data pulled from graph in Hand, 1984 at ~30 days
        # 30 days adjusted to actual solution to account for web plot data extraction error within reason
        # values calculated by hand and match those reported in Hand, 1984
        assert pytest.approx(0.0005178, rel=1e-3) == value(ms.fs.unit.equil_conc)
        assert pytest.approx(19780, rel=1e-3) == value(ms.fs.unit.dg)
        assert pytest.approx(6.113, rel=1e-3) == value(ms.fs.unit.N_Bi)
        assert pytest.approx(35.68, rel=1e-3) == value(ms.fs.unit.min_N_St)
        assert pytest.approx(0.9882, rel=1e-3) == value(ms.fs.unit.throughput)
        assert pytest.approx(468.4, rel=1e-3) == value(ms.fs.unit.min_residence_time)
        assert pytest.approx(134.7, rel=1e-3) == value(ms.fs.unit.residence_time)
        assert pytest.approx(9153000, rel=1e-3) == value(
            ms.fs.unit.min_operational_time
        )
        assert pytest.approx(2554000, rel=1e-3) == value(ms.fs.unit.operational_time)
        assert pytest.approx(8514, rel=1e-3) == value(ms.fs.unit.bed_volumes_treated)


# -----------------------------------------------------------------------------
class TestGACRobust:
    @pytest.fixture(scope="class")
    def gac_frame_robust(self):
        mr = ConcreteModel()
        mr.fs = FlowsheetBlock(dynamic=False)

        mr.fs.properties = MCASParameterBlock(
            solute_list=["TCE"],
            mw_data={"H2O": 0.018, "TCE": 0.1314},
            diffus_calculation=DiffusivityCalculation.HaydukLaudie,
            molar_volume_data={("Liq", "TCE"): 9.81e-5},
        )
        mr.fs.properties.visc_d_phase["Liq"] = 1.3097e-3
        mr.fs.properties.dens_mass_const = 999.7
        mr.fs.unit = GAC(
            property_package=mr.fs.properties,
            film_transfer_coefficient_type="fixed",
            surface_diffusion_coefficient_type="fixed",
            finite_elements_ss_approximation=9,
        )

        # feed specifications
        mr.fs.unit.process_flow.properties_in[0].pressure.fix(
            101325
        )  # feed pressure [Pa]
        mr.fs.unit.process_flow.properties_in[0].temperature.fix(
            273.15 + 25
        )  # feed temperature [K]
        mr.fs.unit.process_flow.properties_in[0].flow_mol_phase_comp["Liq", "H2O"].fix(
            823.8
        )
        mr.fs.unit.process_flow.properties_in[0].flow_mol_phase_comp["Liq", "TCE"].fix(
            5.6444e-05
        )

        # trial problem from Crittenden, 2012 for removal of TCE
        # adsorption isotherm
        mr.fs.unit.freund_k.fix(1062e-6 * (1e6**0.48))
        mr.fs.unit.freund_ninv.fix(0.48)
        # gac particle specifications
        mr.fs.unit.particle_dens_app.fix(803.4)
        mr.fs.unit.particle_dia.fix(0.001026)
        # adsorber bed specifications
        mr.fs.unit.ebct.fix(10 * 60)
        mr.fs.unit.bed_voidage.fix(0.44)
        mr.fs.unit.velocity_sup.fix(5 / 3600)
        # design spec
        mr.fs.unit.conc_ratio_replace.fix(0.80)
        # parameters
        mr.fs.unit.ds.fix(1.24e-14)
        mr.fs.unit.kf.fix(3.73e-05)
        mr.fs.unit.a0.fix(0.8)
        mr.fs.unit.a1.fix(0)
        mr.fs.unit.b0.fix(0.023)
        mr.fs.unit.b1.fix(0.793673)
        mr.fs.unit.b2.fix(0.039324)
        mr.fs.unit.b3.fix(0.009326)
        mr.fs.unit.b4.fix(0.08275)

        return mr

    @pytest.mark.unit
    def test_robust_config(self, gac_frame_robust):
        mr = gac_frame_robust
        # check unit config arguments
        assert len(mr.fs.unit.config) == 12

        assert not mr.fs.unit.config.dynamic
        assert not mr.fs.unit.config.has_holdup
        assert mr.fs.unit.config.material_balance_type == MaterialBalanceType.useDefault
        assert mr.fs.unit.config.energy_balance_type == EnergyBalanceType.none
        assert (
            mr.fs.unit.config.momentum_balance_type == MomentumBalanceType.pressureTotal
        )
        assert (
            mr.fs.unit.config.film_transfer_coefficient_type
            == FilmTransferCoefficientType.fixed
        )
        assert (
            mr.fs.unit.config.surface_diffusion_coefficient_type
            == SurfaceDiffusionCoefficientType.fixed
        )
        assert mr.fs.unit.config.finite_elements_ss_approximation == 9

        # check properties
        assert mr.fs.unit.config.property_package is mr.fs.properties
        assert len(mr.fs.unit.config.property_package.solute_set) == 1
        assert len(mr.fs.unit.config.property_package.solvent_set) == 1
        assert (
            mr.fs.properties.config.diffus_calculation
            == DiffusivityCalculation.HaydukLaudie
        )

    @pytest.mark.unit
    def test_robust_build(self, gac_frame_robust):
        mr = gac_frame_robust

        # test units
        assert assert_units_consistent(mr) is None

        # test ports
        port_lst = ["inlet", "outlet", "adsorbed"]
        for port_str in port_lst:
            port = getattr(mr.fs.unit, port_str)
            assert len(port.vars) == 3  # number of state variables for property package
            assert isinstance(port, Port)

        # test statistics
        assert number_variables(mr) == 119
        assert number_total_constraints(mr) == 84
        assert number_unused_variables(mr) == 10  # dens parameters from properties

    @pytest.mark.unit
    def test_robust_dof(self, gac_frame_robust):
        mr = gac_frame_robust
        assert degrees_of_freedom(mr) == 0

    @pytest.mark.unit
    def test_robust_calculate_scaling(self, gac_frame_robust):
        mr = gac_frame_robust

        mr.fs.properties.set_default_scaling(
            "flow_mol_phase_comp", 1e-2, index=("Liq", "H2O")
        )
        mr.fs.properties.set_default_scaling(
            "flow_mol_phase_comp", 1e5, index=("Liq", "TCE")
        )
        calculate_scaling_factors(mr)

        # check that all variables have scaling factors
        unscaled_var_list = list(unscaled_variables_generator(mr))
        assert len(unscaled_var_list) == 0

    @pytest.mark.component
    def test_robust_initialize(self, gac_frame_robust):
        initialization_tester(gac_frame_robust)

    @pytest.mark.component
    def test_robust_var_scaling_init(self, gac_frame_robust):
        mr = gac_frame_robust
        badly_scaled_var_lst = list(
            badly_scaled_var_generator(mr, large=sv_large, small=sv_small, zero=sv_zero)
        )
        assert badly_scaled_var_lst == []

    @pytest.mark.component
    def test_robust_solve(self, gac_frame_robust):
        mr = gac_frame_robust
        results = solver.solve(mr)

        # Check for optimal solution
        assert check_optimal_termination(results)

    @pytest.mark.component
    def test_robust_var_scaling_solve(self, gac_frame_robust):
        mr = gac_frame_robust
        badly_scaled_var_lst = list(
            badly_scaled_var_generator(mr, large=sv_large, small=sv_small, zero=sv_zero)
        )
        assert badly_scaled_var_lst == []

    @pytest.mark.component
    def test_robust_solution(self, gac_frame_robust):
        mr = gac_frame_robust

        # values calculated by hand and match those reported in Crittenden, 2012
        assert pytest.approx(0.02097, rel=1e-3) == value(mr.fs.unit.equil_conc)
        assert pytest.approx(42890, rel=1e-3) == value(mr.fs.unit.dg)
        assert pytest.approx(45.79, rel=1e-3) == value(mr.fs.unit.N_Bi)
        assert pytest.approx(36.64, rel=1e-3) == value(mr.fs.unit.min_N_St)
        assert pytest.approx(1.139, rel=1e-3) == value(mr.fs.unit.throughput)
        assert pytest.approx(395.9, rel=1e-3) == value(mr.fs.unit.min_residence_time)
        assert pytest.approx(264.0, rel=1e-3) == value(mr.fs.unit.residence_time)
        assert pytest.approx(19340000, rel=1e-3) == value(
            mr.fs.unit.min_operational_time
        )
        assert pytest.approx(13690000, rel=1e-3) == value(mr.fs.unit.operational_time)
        assert pytest.approx(22810, rel=1e-3) == value(mr.fs.unit.bed_volumes_treated)
        assert pytest.approx(0.003157, rel=1e-3) == value(mr.fs.unit.velocity_int)
        assert pytest.approx(0.8333, rel=1e-3) == value(mr.fs.unit.bed_length)
        assert pytest.approx(10.68, rel=1e-3) == value(mr.fs.unit.bed_area)
        assert pytest.approx(8.900, rel=1e-3) == value(mr.fs.unit.bed_volume)
        assert pytest.approx(3.688, rel=1e-3) == value(mr.fs.unit.bed_diameter)
        assert pytest.approx(4004, rel=1e-3) == value(mr.fs.unit.bed_mass_gac)
        assert pytest.approx(6462000, rel=1e-3) == value(
            mr.fs.unit.ele_operational_time[1]
        )
        assert pytest.approx(0.2287, rel=1e-3) == value(mr.fs.unit.conc_ratio_avg)

    @pytest.mark.component
    def test_robust_reporting(self, gac_frame_robust):
        mr = gac_frame_robust
        mr.fs.unit.report()

    @pytest.mark.component
    def test_robust_costing_pressure(self, gac_frame_robust):
        mr = gac_frame_robust

        mr.fs.costing = WaterTAPCosting()
        mr.fs.costing.base_currency = pyo.units.USD_2020

        mr.fs.unit.costing = UnitModelCostingBlock(
            flowsheet_costing_block=mr.fs.costing
        )

        # testing gac costing block dof and initialization
        assert degrees_of_freedom(mr) == 0
        mr.fs.unit.costing.initialize()

        # solve
        results = solver.solve(mr)

        # Check for optimal solution
        assert check_optimal_termination(results)

        # Check for known cost solution of default twin alternating contactors
        assert value(mr.fs.costing.gac.num_contactors_op) == 1
        assert value(mr.fs.costing.gac.num_contactors_redundant) == 1
        assert pytest.approx(56900, rel=1e-3) == value(
            mr.fs.unit.costing.contactor_cost
        )
        assert pytest.approx(4.359, rel=1e-3) == value(
            mr.fs.unit.costing.adsorbent_unit_cost
        )
        assert pytest.approx(17450, rel=1e-3) == value(
            mr.fs.unit.costing.adsorbent_cost
        )
        assert pytest.approx(81690, rel=1e-3) == value(
            mr.fs.unit.costing.other_process_cost
        )
        assert pytest.approx(156000, rel=1e-3) == value(mr.fs.unit.costing.capital_cost)
        assert pytest.approx(12680, rel=1e-3) == value(
            mr.fs.unit.costing.gac_makeup_cost
        )
        assert pytest.approx(27660, rel=1e-3) == value(
            mr.fs.unit.costing.gac_regen_cost
        )
        assert pytest.approx(0.01631, rel=1e-3) == value(
            mr.fs.unit.costing.energy_consumption
        )
        assert pytest.approx(40370, rel=1e-3) == value(
            mr.fs.unit.costing.fixed_operating_cost
        )

    @pytest.mark.component
    def test_robust_costing_gravity(self, gac_frame_robust):
        mr_grav = gac_frame_robust.clone()

        mr_grav.fs.costing = WaterTAPCosting()
        mr_grav.fs.costing.base_currency = pyo.units.USD_2020

        mr_grav.fs.unit.costing = UnitModelCostingBlock(
            flowsheet_costing_block=mr_grav.fs.costing,
            costing_method_arguments={"contactor_type": "gravity"},
        )
        mr_grav.fs.costing.cost_process()
        results = solver.solve(mr_grav)

        # Check for optimal solution
        assert check_optimal_termination(results)

        # Check for known cost solution of default twin alternating contactors
        assert value(mr_grav.fs.costing.gac.num_contactors_op) == 1
        assert value(mr_grav.fs.costing.gac.num_contactors_redundant) == 1
        assert pytest.approx(163200, rel=1e-3) == value(
            mr_grav.fs.unit.costing.contactor_cost
        )
        assert pytest.approx(4.359, rel=1e-3) == value(
            mr_grav.fs.unit.costing.adsorbent_unit_cost
        )
        assert pytest.approx(17450, rel=1e-3) == value(
            mr_grav.fs.unit.costing.adsorbent_cost
        )
        assert pytest.approx(159500, rel=1e-3) == value(
            mr_grav.fs.unit.costing.other_process_cost
        )
        assert pytest.approx(340200, rel=1e-3) == value(
            mr_grav.fs.unit.costing.capital_cost
        )
        assert pytest.approx(12680, rel=1e-3) == value(
            mr_grav.fs.unit.costing.gac_makeup_cost
        )
        assert pytest.approx(27660, rel=1e-3) == value(
            mr_grav.fs.unit.costing.gac_regen_cost
        )
        assert pytest.approx(2.476, rel=1e-3) == value(
            mr_grav.fs.unit.costing.energy_consumption
        )
        assert pytest.approx(40370, rel=1e-3) == value(
            mr_grav.fs.unit.costing.fixed_operating_cost
        )

    @pytest.mark.component
    def test_robust_costing_modular_contactors(self, gac_frame_robust):
        mr = gac_frame_robust

        mr.fs.costing = WaterTAPCosting()
        mr.fs.costing.base_currency = pyo.units.USD_2020

        mr.fs.unit.costing = UnitModelCostingBlock(
            flowsheet_costing_block=mr.fs.costing
        )
        mr.fs.costing.cost_process()

        mr.fs.costing.gac.num_contactors_op.fix(4)
        mr.fs.costing.gac.num_contactors_redundant.fix(2)

        results = solver.solve(mr)

        # Check for known cost solution when changing volume scale of vessels in parallel
        assert value(mr.fs.costing.gac.num_contactors_op) == 4
        assert value(mr.fs.costing.gac.num_contactors_redundant) == 2
        assert pytest.approx(89040, rel=1e-3) == value(
            mr.fs.unit.costing.contactor_cost
        )
        assert pytest.approx(69690, rel=1e-3) == value(
            mr.fs.unit.costing.other_process_cost
        )
        assert pytest.approx(176200, rel=1e-3) == value(mr.fs.unit.costing.capital_cost)

    @pytest.mark.component
    def test_robust_costing_max_gac_ref(self, gac_frame_robust):
        mr = gac_frame_robust

        # scale flow up 10x
        mr.fs.unit.process_flow.properties_in[0].flow_mol_phase_comp["Liq", "H2O"].fix(
            10 * 824.0736620370348
        )
        mr.fs.unit.process_flow.properties_in[0].flow_mol_phase_comp["Liq", "TCE"].fix(
            10 * 5.644342973110135e-05
        )

        mr.fs.costing = WaterTAPCosting()
        mr.fs.costing.base_currency = pyo.units.USD_2020

        mr.fs.unit.costing = UnitModelCostingBlock(
            flowsheet_costing_block=mr.fs.costing
        )
        mr.fs.costing.cost_process()
        # not necessarily an optimum solution because poor scaling but just checking the conditional
        results = solver.solve(mr)

        # Check for bed_mass_gac_cost_ref to be overwritten if bed_mass_gac is greater than bed_mass_gac_cost_max_ref
        assert value(mr.fs.unit.bed_mass_gac) > value(
            mr.fs.costing.gac.bed_mass_max_ref
        )
        assert value(mr.fs.unit.costing.bed_mass_gac_ref) == (
            pytest.approx(value(mr.fs.costing.gac.bed_mass_max_ref), 1e-5)
        )


# -----------------------------------------------------------------------------
class TestGACMulti:
    @pytest.fixture(scope="class")
    def gac_frame_multi(self):
        mm = ConcreteModel()
        mm.fs = FlowsheetBlock(dynamic=False)

        # inserting arbitrary BackGround Solutes, Cations, and Anions to check handling
        # arbitrary diffusivity data for non-target species
        mm.fs.properties = MCASParameterBlock(
            solute_list=["TCE", "BGSOL", "BGCAT", "BGAN"],
            mw_data={
                "H2O": 0.018,
                "TCE": 0.1314,
                "BGSOL": 0.1,
                "BGCAT": 0.1,
                "BGAN": 0.1,
            },
            charge={"BGCAT": 1, "BGAN": -2},
            diffus_calculation=DiffusivityCalculation.HaydukLaudie,
            molar_volume_data={("Liq", "TCE"): 9.81e-5},
            diffusivity_data={
                ("Liq", "BGSOL"): 1e-5,
                ("Liq", "BGCAT"): 1e-5,
                ("Liq", "BGAN"): 1e-5,
            },
        )
        mm.fs.properties.visc_d_phase["Liq"] = 1.3097e-3
        mm.fs.properties.dens_mass_const = 1000
        # testing target_species arg
        mm.fs.unit = GAC(
            property_package=mm.fs.properties,
            film_transfer_coefficient_type="calculated",
            surface_diffusion_coefficient_type="calculated",
            target_species={"TCE"},
        )

        # feed specifications
        mm.fs.unit.process_flow.properties_in[0].pressure.fix(
            101325
        )  # feed pressure [Pa]
        mm.fs.unit.process_flow.properties_in[0].temperature.fix(
            273.15 + 25
        )  # feed temperature [K]
        mm.fs.unit.process_flow.properties_in[0].flow_mol_phase_comp["Liq", "H2O"].fix(
            824.0736620370348
        )
        mm.fs.unit.process_flow.properties_in[0].flow_mol_phase_comp["Liq", "TCE"].fix(
            5.644342973110135e-05
        )
        mm.fs.unit.process_flow.properties_in[0].flow_mol_phase_comp[
            "Liq", "BGSOL"
        ].fix(5e-05)
        mm.fs.unit.process_flow.properties_in[0].flow_mol_phase_comp[
            "Liq", "BGCAT"
        ].fix(2e-05)
        mm.fs.unit.process_flow.properties_in[0].flow_mol_phase_comp["Liq", "BGAN"].fix(
            1e-05
        )

        # trial problem from Crittenden, 2012 for removal of TCE
        # adsorption isotherm
        mm.fs.unit.freund_k.fix(1062e-6 * (1e6**0.48))
        mm.fs.unit.freund_ninv.fix(0.48)
        # gac particle specifications
        mm.fs.unit.particle_dens_app.fix(803.4)
        mm.fs.unit.particle_dia.fix(0.001026)
        # adsorber bed specifications
        mm.fs.unit.ebct.fix(10 * 60)
        mm.fs.unit.bed_voidage.fix(0.44)
        mm.fs.unit.velocity_sup.fix(5 / 3600)
        # design spec
        mm.fs.unit.conc_ratio_replace.fix(0.80)
        # parameters
        mm.fs.unit.particle_porosity.fix(0.641)
        mm.fs.unit.tort.fix(1)
        mm.fs.unit.spdfr.fix(1)
        mm.fs.unit.shape_correction_factor.fix(1.5)
        mm.fs.unit.a0.fix(0.8)
        mm.fs.unit.a1.fix(0)
        mm.fs.unit.b0.fix(0.023)
        mm.fs.unit.b1.fix(0.793673)
        mm.fs.unit.b2.fix(0.039324)
        mm.fs.unit.b3.fix(0.009326)
        mm.fs.unit.b4.fix(0.08275)

        return mm

    @pytest.mark.unit
    def test_multi_config(self, gac_frame_multi):
        mm = gac_frame_multi

        # checking non-unity solute set and nonzero ion set handling
        assert len(mm.fs.unit.config.property_package.solute_set) == 4
        assert len(mm.fs.unit.config.property_package.solvent_set) == 1
        assert len(mm.fs.unit.config.property_package.ion_set) == 2
        assert (
            mm.fs.properties.config.diffus_calculation
            == DiffusivityCalculation.HaydukLaudie
        )
        assert (
            mm.fs.unit.config.film_transfer_coefficient_type
            == FilmTransferCoefficientType.calculated
        )
        assert (
            mm.fs.unit.config.surface_diffusion_coefficient_type
            == SurfaceDiffusionCoefficientType.calculated
        )

        assert degrees_of_freedom(mm) == 0

    @pytest.mark.unit
    def test_multi_calculate_scaling(self, gac_frame_multi):
        mm = gac_frame_multi

        mm.fs.properties.set_default_scaling(
            "flow_mol_phase_comp", 1e-2, index=("Liq", "H2O")
        )
        for j in mm.fs.properties.ion_set | mm.fs.properties.solute_set:
            mm.fs.properties.set_default_scaling(
                "flow_mol_phase_comp", 1e5, index=("Liq", j)
            )

        calculate_scaling_factors(mm)
        initialization_tester(gac_frame_multi)

        # check that all variables have scaling factors
        unscaled_var_list = list(unscaled_variables_generator(mm))
        assert len(unscaled_var_list) == 0

    @pytest.mark.unit
    def test_multi_var_scaling_init(self, gac_frame_multi):
        mm = gac_frame_multi
        badly_scaled_var_lst = list(
            badly_scaled_var_generator(mm, large=sv_large, small=sv_small, zero=sv_zero)
        )
        assert badly_scaled_var_lst == []

    @pytest.mark.component
    def test_multi_solve(self, gac_frame_multi):
        mm = gac_frame_multi
        results = solver.solve(mm)

        # Check for optimal solution
        assert check_optimal_termination(results)

    @pytest.mark.unit
    def test_multi_var_scaling_solve(self, gac_frame_multi):
        mm = gac_frame_multi
        badly_scaled_var_lst = list(
            badly_scaled_var_generator(mm, large=sv_large, small=sv_small, zero=sv_zero)
        )
        assert badly_scaled_var_lst == []

    @pytest.mark.component
    def test_multi_solution(self, gac_frame_multi):
        mm = gac_frame_multi

        # only checking for variables new to configuration options
        assert pytest.approx(2.473, rel=1e-3) == value(mm.fs.unit.N_Re)
        assert pytest.approx(2001, rel=1e-3) == value(mm.fs.unit.N_Sc)
        assert pytest.approx(2.600e-5, rel=1e-3) == value(mm.fs.unit.kf)
        assert pytest.approx(1.245e-14, rel=1e-3) == value(mm.fs.unit.ds)

    @pytest.mark.component
    def test_multi_reporting(self, gac_frame_multi):
        mm = gac_frame_multi
        mm.fs.unit.report()


# -----------------------------------------------------------------------------
class TestGACErrorLog:
    @pytest.mark.unit
    def test_error(self):

        with pytest.raises(
            ConfigurationError,
            match="'target species' is not specified for the GAC unit model, "
            "either specify 'target species' argument or reduce solute set "
            "to a single component",
        ):
            me = ConcreteModel()
            me.fs = FlowsheetBlock(dynamic=False)

            # inserting arbitrary BackGround Solutes, Cations, and Anions to check handling
            # arbitrary diffusivity data for non-target species
            me.fs.properties = MCASParameterBlock(
                solute_list=["TCE", "BGSOL", "BGCAT", "BGAN"],
                mw_data={
                    "H2O": 0.018,
                    "TCE": 0.1314,
                    "BGSOL": 0.1,
                    "BGCAT": 0.1,
                    "BGAN": 0.1,
                },
                charge={"BGCAT": 1, "BGAN": -2},
                diffus_calculation=DiffusivityCalculation.HaydukLaudie,
                molar_volume_data={("Liq", "TCE"): 9.81e-5},
                diffusivity_data={
                    ("Liq", "BGSOL"): 1e-5,
                    ("Liq", "BGCAT"): 1e-5,
                    ("Liq", "BGAN"): 1e-5,
                },
            )
            me.fs.properties.visc_d_phase["Liq"] = 1.3097e-3
            me.fs.properties.dens_mass_const = 1000

            # testing target_species arg
            me.fs.unit = GAC(
                property_package=me.fs.properties,
                film_transfer_coefficient_type="calculated",
                surface_diffusion_coefficient_type="calculated",
            )

        with pytest.raises(
            ConfigurationError,
            match="fs.unit received invalid argument for contactor_type:"
            " vessel. Argument must be a member of the ContactorType Enum.",
        ):
            me = ConcreteModel()
            me.fs = FlowsheetBlock(dynamic=False)

            me.fs.properties = MCASParameterBlock(
                solute_list=["TCE"],
                mw_data={"H2O": 0.018, "TCE": 0.1314},
                diffus_calculation=DiffusivityCalculation.HaydukLaudie,
                molar_volume_data={("Liq", "TCE"): 9.81e-5},
            )
            me.fs.properties.visc_d_phase["Liq"] = 1.3097e-3
            me.fs.properties.dens_mass_const = 1000

            me.fs.unit = GAC(
                property_package=me.fs.properties,
                film_transfer_coefficient_type="calculated",
                surface_diffusion_coefficient_type="calculated",
            )

            me.fs.costing = WaterTAPCosting()
            me.fs.costing.base_currency = pyo.units.USD_2020

            me.fs.unit.costing = UnitModelCostingBlock(
                flowsheet_costing_block=me.fs.costing,
                costing_method_arguments={"contactor_type": "vessel"},
            )

        with pytest.raises(
            ConfigurationError,
            match="item 0 within 'target_species' list is not of data type str",
        ):
            me = ConcreteModel()
            me.fs = FlowsheetBlock(dynamic=False)

            # inserting arbitrary BackGround Solutes, Cations, and Anions to check handling
            # arbitrary diffusivity data for non-target species
            me.fs.properties = MCASParameterBlock(
                solute_list=["TCE", "BGSOL", "BGCAT", "BGAN"],
                mw_data={
                    "H2O": 0.018,
                    "TCE": 0.1314,
                    "BGSOL": 0.1,
                    "BGCAT": 0.1,
                    "BGAN": 0.1,
                },
                charge={"BGCAT": 1, "BGAN": -2},
                diffus_calculation=DiffusivityCalculation.HaydukLaudie,
                molar_volume_data={("Liq", "TCE"): 9.81e-5},
                diffusivity_data={
                    ("Liq", "BGSOL"): 1e-5,
                    ("Liq", "BGCAT"): 1e-5,
                    ("Liq", "BGAN"): 1e-5,
                },
            )
            me.fs.properties.visc_d_phase["Liq"] = 1.3097e-3
            me.fs.properties.dens_mass_const = 1000

            # testing target_species arg
            me.fs.unit = GAC(
                property_package=me.fs.properties,
                film_transfer_coefficient_type="calculated",
                surface_diffusion_coefficient_type="calculated",
                target_species=range(2),
            )

        with pytest.raises(
            ConfigurationError,
            match="item species within 'target_species' list is not in 'component_list",
        ):
            me = ConcreteModel()
            me.fs = FlowsheetBlock(dynamic=False)

            # inserting arbitrary BackGround Solutes, Cations, and Anions to check handling
            # arbitrary diffusivity data for non-target species
            me.fs.properties = MCASParameterBlock(
                solute_list=["TCE", "BGSOL", "BGCAT", "BGAN"],
                mw_data={
                    "H2O": 0.018,
                    "TCE": 0.1314,
                    "BGSOL": 0.1,
                    "BGCAT": 0.1,
                    "BGAN": 0.1,
                },
                charge={"BGCAT": 1, "BGAN": -2},
                diffus_calculation=DiffusivityCalculation.HaydukLaudie,
                molar_volume_data={("Liq", "TCE"): 9.81e-5},
                diffusivity_data={
                    ("Liq", "BGSOL"): 1e-5,
                    ("Liq", "BGCAT"): 1e-5,
                    ("Liq", "BGAN"): 1e-5,
                },
            )
            me.fs.properties.visc_d_phase["Liq"] = 1.3097e-3
            me.fs.properties.dens_mass_const = 1000

            # testing target_species arg
            me.fs.unit = GAC(
                property_package=me.fs.properties,
                film_transfer_coefficient_type="calculated",
                surface_diffusion_coefficient_type="calculated",
                target_species={"species": "TCE"},
            )
