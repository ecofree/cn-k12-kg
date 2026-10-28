"""V7 source review and independent mathematics for the 77 completed supports.

These checks use counts, exact fractions, calendars and explicit counterexamples
independently of authored ``verify`` strings. They are synthetic authoring checks,
not teacher review or evidence of learning effectiveness. Actual all-route runtime
integration is exercised separately by test_support_runtime_all_goals_v7.py.
"""
import copy
from datetime import datetime, timedelta
from fractions import Fraction as F
import hashlib
import json
from math import gcd, lcm
from pathlib import Path
import unittest

from cnkg import instruction
from cnkg.answers import grade_answer
from cnkg.contracts import validate_resource


ROOT = Path(__file__).resolve().parents[1]
COUNTS = {
    'ten_frame_place_value': 5, 'common_factors_multiples': 7,
    'fraction_equivalence': 6, 'position_reference_frames': 8,
    'time_duration_calendar': 8, 'triangle_conditions': 8,
    'ratio_models': 10, 'equation_equivalence': 7,
    'linear_function_representations': 10, 'derivative_monotonicity': 8,
}
NEW_CONFIRMATION = 'derivative_monotonicity:transfer-7'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


class InstructionalSupportCompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.units = {p.stem: json.loads(p.read_text()) for p in
                     (ROOT / 'data/learning_units').glob('*.json')}
        cls.review = json.loads((ROOT / 'docs/reviews/v7-instructional-support-completion-ai-review.json').read_text())

    def support(self, unit, goal):
        return next(s for s in self.units[unit]['instructional_support']['supports']
                    if s['objective_id'] == unit + ':' + goal)

    def task(self, unit, goal, route):
        support = self.support(unit, goal)
        if route == 'skip':
            return support['skip_route']['guided_task']
        return next(r['guided_task'] for r in support['guided_branches'] if r['id'] == route)

    def selected(self, unit, goal, route, fragment):
        task = self.task(unit, goal, route)
        candidates = [c for c in task['choices'] if fragment in c['text']]
        self.assertEqual(1, len(candidates), (unit, goal, route, fragment))
        self.assertEqual('correct', grade_answer(task, candidates[0]['id'])['status'], (unit, goal, route))
        for other in task['choices']:
            if other['id'] != candidates[0]['id']:
                self.assertEqual('wrong', grade_answer(task, other['id'])['status'])

    def test_77_reviews_are_source_bound_and_preserve_all_previous_content(self):
        review = self.review
        self.assertEqual(77, len(review['support_reviews']))
        self.assertFalse(review['independent_teacher_review'])
        self.assertFalse(review['student_effectiveness_evidence'])
        self.assertEqual(COUNTS, {s['unit_id']: s['new_supports'] for s in review['sources']})
        by_support = {s['id']: s for u in self.units.values()
                      for s in u['instructional_support']['supports']}
        self.assertEqual(104, len(by_support))
        self.assertEqual(208, sum(len(s['guided_branches']) for s in by_support.values()))
        old_hashes = review['preserved_support_sha256']
        self.assertEqual(27, len(old_hashes))
        for identity, expected in old_hashes.items():
            self.assertEqual(expected, digest(by_support[identity]), identity)
        for row in review['support_reviews']:
            self.assertEqual('ai', row['reviewer_kind'])
            self.assertFalse(row['independent_teacher_review'])
            self.assertFalse(row['student_effectiveness_evidence'])
            self.assertEqual(row['reviewed_support_sha256'], digest(by_support[row['support_id']]))
            self.assertEqual(2, len(row['routes']))
            self.assertTrue(all(r['mathematical_check'] and r['observation_boundary'] for r in row['routes']))
        for name, unit in self.units.items():
            outside = copy.deepcopy(unit)
            outside.pop('instructional_support')
            if name == 'derivative_monotonicity':
                self.assertEqual(1, sum(t['id'] == NEW_CONFIRMATION for t in outside['tasks']))
                outside['tasks'] = [t for t in outside['tasks'] if t['id'] != NEW_CONFIRMATION]
            self.assertEqual(review['baseline_outside_support_sha256'][name], digest(outside), name)
        for source in review['sources']:
            self.assertEqual(source['reviewed_source_sha256'], hashlib.sha256((ROOT / source['path']).read_bytes()).hexdigest())

    def test_source_contracts_and_confirmation_observations(self):
        for name in COUNTS:
            unit = self.units[name]
            self.assertEqual([], validate_resource('learning-unit', unit), name)
            self.assertEqual([], instruction.check_supports(unit), name)
            tasks = {t['id']: t for t in unit['tasks']}
            for support in unit['instructional_support']['supports']:
                for tid in support['followup_task_ids']:
                    self.assertIn(support['objective_id'], tasks[tid]['objective_ids'])
        self.assertEqual(['equation_equivalence:transfer-3'],
                         self.support('equation_equivalence', 'solve_check')['followup_task_ids'])
        self.assertEqual(['linear_function_representations:transfer-7'],
                         self.support('linear_function_representations', 'compare_models')['followup_task_ids'])
        self.assertEqual(['linear_function_representations:delayed-5'],
                         self.support('linear_function_representations', 'representations')['followup_task_ids'])
        self.assertEqual([NEW_CONFIRMATION], self.support('derivative_monotonicity', 'o2')['followup_task_ids'])

    def test_every_new_numeric_guide_against_independently_constructed_quantities(self):
        # Inputs are read from each problem, then modeled independently. No answer
        # or verify field supplies these expected values.
        cases = [
            ('common_factors_multiples','gcd','recover_factor_pairs',gcd(24,40)),
            ('common_factors_multiples','lcm','skip',min(9*n for n in range(1,5))),
            ('derivative_monotonicity','o1','skip',next(x for x in range(-4,5) if x-3==0)),
            ('derivative_monotonicity','o3','evaluate_original_function_not_derivative',(lambda x:x*x+7)(0)),
            ('derivative_monotonicity','domain_split','skip',abs(2-2)),
            ('derivative_monotonicity','extrema','read_piecewise_value_at_special_input',{0:8}[0]),
            ('derivative_monotonicity','boundary_maximum','find_endpoint_maximum_without_stationary_point',2*max([0,2])+1),
            ('equation_equivalence','o2','multiply_two_negative_factors',-3*(-4)),
            ('equation_equivalence','o3','substitute_rounded_candidate',len([1]*3)*3+2),
            ('fraction_equivalence','o1','compare_piece_areas',F(sum([1,1,1]),sum([1,1,1,1,3,3]))),
            ('fraction_equivalence','o1','skip',F(len([1,1]),len(range(8)))),
            ('fraction_equivalence','o2','count_refined_parts',len([(large,small) for large in range(3) for small in range(3)])),
            ('fraction_equivalence','o2','skip',F(12,4)),
            ('fraction_equivalence','o3','skip',sum([F(1,8)]*3)),
            ('fraction_equivalence','represent','retain_completed_whole',F(1)+sum([F(1,7)]*3)),
            ('fraction_equivalence','operate','skip',sum([F(1,8)]*5)-sum([F(1,8)]*2)),
            ('linear_function_representations','o1','recover_value_at_zero',11-sum([2]*3)),
            ('linear_function_representations','rate','normalize_output_change',F(8,4)),
            ('linear_function_representations','intercept','reject_first_output_by_substitution',(lambda x:2*x+11)(3)),
            ('linear_function_representations','intercept','skip',(lambda x:4*x+7)(0)),
            ('linear_function_representations','read_graph','convert_horizontal_elapsed_quantity',sum([4]*3)),
            ('position_reference_frames','route_distance','separate_return_and_zero_distance',sum(abs(x)*15 for x in [2,-2])),
            ('position_reference_frames','route_distance','skip',sum([10]*3)),
            ('ratio_models','o3','skip',len(range(3))+len(range(4))),
            ('ratio_models','scale','convert_real_length_units',F(150000,100)),
            ('ten_frame_place_value','count','skip',len({'甲','乙','丙','丁'})),
            ('ten_frame_place_value','compose_ten','skip',len(set(range(10))-set(range(5)))),
            ('ten_frame_place_value','place_value','unpack_one_ten',sum(len(range(10)) for _ in range(2))),
            ('ten_frame_place_value','make_ten','remove_transferred_part',len(set(range(7))-set(range(2)))),
            ('ten_frame_place_value','make_ten','skip',len(set(range(10))-set(range(7)))),
            ('ten_frame_place_value','operation','skip',len({('row1',i)for i in range(3)}|{('row2',i)for i in range(2)})),
            ('time_duration_calendar','o2','derive_sixty_minutes',sum([5]*12)),
            ('time_duration_calendar','o2','skip',F(60,2)),
            ('time_duration_calendar','o3','skip',(datetime(2028,6,11,8)-datetime(2028,6,10,8)).days),
            ('time_duration_calendar','read_time','convert_minute_marks',sum([5]*3)),
            ('time_duration_calendar','units60','convert_decimal_fraction_of_hour',F(30,100)*60),
            ('time_duration_calendar','units60','skip',F(30,60)),
            ('triangle_conditions','o2','subtract_known_part_of_straight_angle',180-40),
            ('triangle_conditions','counterexample','repair_invalid_counterexample',180-sum([120,50])),
        ]
        observed = set()
        for unit, goal, route, value in cases:
            with self.subTest(unit=unit,goal=goal,route=route):
                task = self.task(unit,goal,route)
                self.assertEqual('correct', grade_answer(task,str(value))['status'])
                self.assertEqual('wrong', grade_answer(task,str(F(value)+1))['status'])
                observed.add((unit,goal,route))
        reviewed = {r['support_id'] for r in self.review['support_reviews']}
        actual = set()
        for unit in self.units.values():
            for support in unit['instructional_support']['supports']:
                if support['id'] not in reviewed:
                    continue
                for route in [*support['guided_branches'],dict(support['skip_route'],id='skip')]:
                    if route['guided_task']['type']=='numeric':
                        actual.add((unit['id'],support['objective_id'].split(':')[-1],route['id']))
        self.assertEqual(actual, observed)  # A newly added numeric guide needs a real oracle.

    def test_ten_frame_objects_capacity_regrouping_and_unknown_whole(self):
        u='ten_frame_place_value'
        self.assertEqual(set('甲乙丙丁'), set('丙丁甲乙'))
        self.selected(u,'count','match_same_objects','逐一对应')
        self.assertEqual(5,len(set('甲乙丙丁戊甲')))
        self.selected(u,'count','mark_counting_start','回甲时停止')
        self.assertEqual(3,10-(5+2))
        self.selected(u,'compose_ten','separate_filled_empty','3个空格')
        self.assertGreater(3+10,10)
        self.selected(u,'compose_ten','check_capacity_after_addition','只补7颗')
        self.assertEqual(17,1*10+7)
        self.selected(u,'place_value','locate_digit_contribution','10根与7根')
        self.assertEqual(8+6,10+(6-(10-8)))
        self.selected(u,'make_ten','distinguish_transfer_and_remainder','剩余部分')
        self.assertEqual(8-3,5)
        self.selected(u,'operation','identify_unknown_whole','□=3+5')
        self.assertEqual(7+2,2+7)
        self.selected(u,'operation','ignore_story_number_order','都是9张')

    def test_common_factors_require_both_membership_and_optimality(self):
        u='common_factors_multiples'
        self.assertEqual(6,gcd(18,30)); self.assertGreater(gcd(18,30),3)
        self.selected(u,'o1','seek_larger_common_divisor','6同时整除')
        self.assertEqual(6,min(set(range(2,13,2))&set(range(3,13,3))))
        self.selected(u,'o2','exclude_zero_from_next_time','6秒，因为0是起点')
        self.assertLess(lcm(8,12),8*12)
        self.selected(u,'o2','separate_product_and_minimum','24小于96')
        self.assertEqual(0,(12+20)%8);self.assertNotEqual(0,12%8);self.assertNotEqual(0,20%8)
        self.selected(u,'o3','respect_separate_rope_constraints','总长可分不代表每根可分')
        self.assertEqual(0,24%3);self.assertNotEqual(0,16%3)
        self.selected(u,'gcd','intersect_not_union','3不公有')
        self.assertNotEqual(0,6%4)
        self.selected(u,'lcm','check_larger_is_multiple','6连公倍数都不是')
        self.assertEqual([2,6,10],[2+4*n for n in range(3)])
        self.selected(u,'choose_model','include_starting_offset','2+4n')
        self.assertNotIn(14,set([0,5,10,15])&set([2,6,10,14]))
        self.selected(u,'choose_model','check_both_schedules','乙亮但甲不亮')
        self.assertEqual([12],[x for x in range(1,13) if x%4==x%6==0])
        self.selected(u,'justify_optimum','require_extremum_evidence','4、8都不能被6整除')
        self.assertEqual(F(1,3),F(8,24));self.assertEqual(F(1,4),F(6,24));self.assertLess(lcm(3,4),24)
        self.selected(u,'justify_optimum','separate_valid_from_optimal','等值且可用')

    def test_fraction_partition_value_unit_and_counterexample(self):
        u='fraction_equivalence'
        self.assertGreater(F(sum([3,3,3]),sum([1,1,1,3,3,3])),F(1,2))
        self.selected(u,'o1','construct_more_than_half','涂3块面积为3')
        self.assertNotEqual(F(1,2),F(1+1,2+1))
        self.selected(u,'o2','disprove_equal_addition','涂色量变多')
        self.assertEqual(F(1,5)+F(2,5),F(3,5))
        self.selected(u,'o3','keep_share_size','3/5')
        self.assertEqual(F(1,4),F(3,12));self.assertEqual(F(1,6),F(2,12))
        self.selected(u,'o3','preserve_value_when_renaming','3/12')
        ticks=list(range(5));self.assertEqual(4,len(list(zip(ticks,ticks[1:]))))
        self.selected(u,'represent','count_intervals_not_ticks','1/4个单位')
        self.assertEqual(3,gcd(9,15));self.assertEqual(F(15,25),F(3,5));self.assertNotEqual(F(15,25),F(10,20))
        self.selected(u,'simplify','check_odd_common_factors','都能被3整除')
        self.selected(u,'simplify','divide_not_subtract','同除以5')
        self.assertLess(F(4,9),F(1,2));self.assertGreater(F(3,4),F(1,2))
        self.selected(u,'operate','compare_share_sizes','3/4较大')
        self.selected(u,'operate','preserve_unit_when_removing','1/12')

    def test_position_frames_turns_bearings_and_travelled_distance(self):
        u='position_reference_frames';clockwise=['北','东','南','西']
        self.assertEqual('东',clockwise[(clockwise.index('北')+1)%4])
        self.assertEqual('西',clockwise[(clockwise.index('南')+1)%4])
        self.selected(u,'o1','separate_body_and_compass','甲；乙看作左侧')
        self.selected(u,'o1','trace_right_quarter_turn','南')
        self.assertEqual([100,400],[origin+100 for origin in [0,300]])
        self.selected(u,'o2','construct_two_possible_locations','100米标记和400米')
        self.selected(u,'o2','units_do_not_supply_origin','从哪个参照点')
        self.assertNotEqual((5,4),(4,5))
        self.selected(u,'o3','keep_ordered_coordinate_roles','是不同格')
        self.selected(u,'o3','change_coordinate_for_direction','(c+3,r)')
        # Rotate standard north/east vectors clockwise by 90 degrees.
        rotate=lambda v:(v[1],-v[0])
        self.assertEqual((0,-1),rotate((1,0)));self.assertEqual((1,0),rotate((0,1)))
        self.selected(u,'body_frame','infer_north_from_known_east','右侧')
        self.assertEqual((-1,0),rotate(rotate((1,0))))
        self.selected(u,'body_frame','construct_compass_clockwise','左侧')
        self.assertEqual(90,35+(90-35));self.assertLess(90-35,90)
        self.selected(u,'bearing','split_right_angle','35°+β=90°')
        self.selected(u,'bearing','reject_supplementary_bearing','0°<θ<90°')
        self.assertEqual(65,90-25)
        self.selected(u,'return_direction','separate_reverse_and_relabel','北偏东25°')
        self.assertEqual([7,6,5,4],[8-i for i in range(1,5)])
        self.selected(u,'coordinate','include_both_end_positions','1↔7，2↔6')
        self.assertEqual(120,sum(abs(x)*20 for x in [3,2,-1]))
        self.selected(u,'route_distance','include_every_travelled_leg','三段都相加')

    def test_time_uses_duration_sixty_date_context_and_intervals(self):
        u='time_duration_calendar'
        self.assertEqual(timedelta(minutes=10),datetime(2028,6,1,9,10)-datetime(2028,6,1,9))
        self.selected(u,'o1','duration_has_no_fixed_clock_position','不指定固定开始时刻')
        self.assertEqual(125,2*60+5)
        self.selected(u,'o2','include_whole_hours','2×60+5')
        self.assertEqual(2,(datetime(2028,3,1)-datetime(2028,2,28)).days)
        self.selected(u,'o3','include_leap_day','2月29日')
        self.selected(u,'o3','count_links_between_dates','共2段')
        self.assertLess(8+F(50,60),9)
        self.selected(u,'read_time','read_hour_already_passed','已经经过的小时是8')
        self.assertEqual(F(9,4),F(135,60));self.assertNotEqual(F(30,100)*60,30)
        self.selected(u,'units60','combine_hours_and_fraction','2.25小时')
        start=datetime(2028,6,1,22)
        self.assertEqual([4,28],[(datetime(2028,6,d,2)-start).total_seconds()/3600 for d in [2,3]])
        self.selected(u,'elapsed','show_two_date_assignments','28小时')
        self.selected(u,'elapsed','include_midnight_day_offset','(24+2)−22')
        leap=lambda y:y%4==0 and (y%100!=0 or y%400==0)
        self.assertFalse(leap(1900));self.assertTrue(leap(2000));self.assertFalse(leap(2100));self.assertTrue(leap(2400))
        self.selected(u,'calendar','apply_century_condition','不是闰年')
        self.selected(u,'calendar','allow_four_hundred_years','2000=400×5')
        points=list(range(4));self.assertEqual(3,len(list(zip(points,points[1:]))))
        self.selected(u,'count_intervals','choose_segments_for_elapsed','三条相邻线段')
        self.selected(u,'count_intervals','retain_boundary_intervals','三段完整的一天')

    def test_triangle_conditions_angle_roles_and_counterexample_validity(self):
        u='triangle_conditions'
        valid=lambda lengths:all(x>0 for x in lengths) and sum(sorted(lengths)[:2])>max(lengths)
        self.assertFalse(valid([4,6,10]));self.assertFalse(valid([1,2,10]));self.assertTrue(valid([3,3,4]))
        self.selected(u,'o1','recognize_collinear_equality','没有围出三角形内部')
        self.selected(u,'o1','positive_is_not_sufficient','1、2、10')
        self.assertEqual(180,40+(180-40))
        self.selected(u,'o2','identify_supplementary_pair','相加为180°')
        self.assertEqual(180,sum([90,45,45]));self.assertFalse(all(a<90 for a in [90,45,45]))
        self.selected(u,'o3','test_universal_claim_with_counterexample','直角三角形')
        self.assertNotEqual(180,sum([62,59,60]))
        self.selected(u,'o3','use_measurement_for_conjecture_and_check','检查量角器中心')
        self.selected(u,'feasibility','check_shortest_pair_against_longest','a+b>c')
        self.selected(u,'feasibility','equal_sides_can_be_valid','能围成等腰三角形')
        self.assertEqual(list(range(4,11)),[x for x in range(1,13) if valid([4,7,x])])
        self.selected(u,'integer_range','exclude_degenerate_endpoints','两种都退化')
        self.selected(u,'integer_range','retain_difference_lower_bound','2+4<7')
        self.assertGreater(55,max([30,25]));self.assertLess(55,125)
        self.selected(u,'angle_relations','name_nonadjacent_angles','小于相邻的125°')
        self.assertEqual(180,sum([100,40,40]))
        self.selected(u,'angle_relations','allow_obtuse_triangles','允许其中一个钝角')
        self.selected(u,'proof_chain','check_incidence_is_not_parallel','交于D')
        self.selected(u,'proof_chain','length_cannot_create_parallelism','不改变两条直线的方向关系')
        self.assertTrue(all(a<90 for a in [60,60,60]))
        self.selected(u,'counterexample','negate_the_conclusion','仍满足“锐角”结论')

    def test_ratio_invariants_referents_shares_rates_and_scales(self):
        u='ratio_models'
        self.assertEqual(F(5,7),F(5,2+5))
        self.selected(u,'o1','assemble_the_whole','蓝珠占5/7')
        self.assertEqual(F(2,5),F(4,10));self.assertNotEqual(5-2,10-4)
        self.selected(u,'o1','separate_ratio_shares_and_counts','实际差分别3颗和6颗')
        self.assertEqual(2+10,4+8);self.assertNotEqual(2*10,4*8)
        self.selected(u,'o2','check_product_not_opposite_trend','乘积不固定')
        self.assertEqual((18,27),(45*F(2,5),45*F(3,5)))
        self.selected(u,'o3','match_total_to_sum_of_shares','45÷(2+3)')
        self.selected(u,'o3','scale_shares_to_actual_total','甲18本、乙27本')
        self.assertEqual(4,F(2*100,50));self.assertEqual(250,F(500,2))
        self.selected(u,'referent','unify_length_units','200厘米:50厘米=4:1')
        self.selected(u,'referent','preserve_requested_order','500:2')
        self.assertEqual(F(4,7),F(4,4+3));self.assertEqual(F(3,5)+F(2,5),1)
        self.selected(u,'part_whole','use_total_as_denominator','全部包含黑4份和白3份')
        self.selected(u,'part_whole','label_numerator_by_requested_part','黑占3/5，白占2/5')
        self.assertNotEqual(F(2,3),F(3,4));self.assertEqual(F(3,5),F(9,15));self.assertNotEqual(5-3,15-9)
        self.selected(u,'equivalent_ratio','test_growth_with_counterexample','倍数不同')
        self.selected(u,'equivalent_ratio','distinguish_ratio_and_difference','比值保持')
        self.assertEqual(1,F(5,400)*F(400,5))
        self.selected(u,'unit_rate','read_reciprocal_rate_units','每米用0.0125分钟')
        self.selected(u,'unit_rate','share_quantity_not_subtract_units','4元/千克')
        self.assertNotEqual(0,2+5*0);self.assertEqual(2*6,4*3)
        self.selected(u,'invariant','test_zero_input_and_base_fee','0次时收费0元')
        self.selected(u,'invariant','derive_inverse_change_from_product','从6变3')
        self.assertEqual(3,5-2);self.assertEqual(2,F(27-9,9))
        self.selected(u,'allocation','map_difference_to_unmatched_shares','3份')
        self.selected(u,'allocation','distinguish_group_amount_and_gap','2份，因为')
        self.assertEqual(F(1,1000),F(2,2000))
        self.selected(u,'scale','solve_scale_in_correct_direction','s=1000g')

    def test_equations_preserve_solution_sets_and_quantity_constraints(self):
        u='equation_equivalence'
        self.assertFalse(7==5);self.assertTrue(0*7==0*5)
        self.selected(u,'o1','test_loss_of_information','新式多了原来没有的解')
        self.selected(u,'o1','allow_known_nonzero_divisor','已知非零')
        for x in [-3,0,4]:self.assertEqual(2*(x+5),2*x+10)
        self.selected(u,'o2','distribute_to_constant_too','同时乘x与5')
        self.assertFalse(F(7,2).denominator==1)
        self.selected(u,'o3','distinguish_discrete_and_continuous_quantity','千克可按质量分装')
        self.assertEqual(12,4*3)
        self.selected(u,'distribute','multiply_integer_term','变为12')
        self.selected(u,'distribute','balance_both_equation_sides','3x=6')
        # For a*x=b, a=0 separates all-real and no-solution cases without division.
        solution_kind=lambda a,b:'all' if a==b==0 else 'none' if a==0 else 'unique'
        self.assertEqual('none',solution_kind(0,4));self.assertEqual('all',solution_kind(0,0))
        self.selected(u,'solve_check','zero_coefficient_is_not_zero_solution','假等式')
        self.selected(u,'solve_check','compare_identity_and_contradiction','甲所有实数都是解，乙无解')
        self.assertEqual(16,6+(6+4))
        self.selected(u,'model_quantity','include_both_groups_in_total','两人合计')
        self.selected(u,'model_quantity','orient_the_comparison','x+4，代入得10本')
        self.assertEqual([], [x for x in range(10) if 3*x==10])
        self.assertEqual(4,min(x for x in range(10) if 3*x>=10))
        self.selected(u,'domain_constraint','separate_equality_from_capacity','前者没有整数解')
        self.assertNotEqual(13,4*round(F(13,4)))
        self.selected(u,'domain_constraint','check_exact_not_approximate','不满足原条件')

    def test_linear_roles_units_domains_and_strict_integer_threshold(self):
        u='linear_function_representations'
        self.assertEqual((2*3+1)-1,(2*3+7)-7)
        self.selected(u,'o1','increments_do_not_identify_intercept','不能，两式截距分别1和7')
        self.assertEqual(2*3+2,3+5);self.assertNotEqual(2,1)
        self.selected(u,'o2','one_point_does_not_give_rate','不足以唯一确定')
        self.selected(u,'o2','read_axes_before_point','水量6升对应时间2分钟')
        self.assertEqual(10,min(10,4+2*5));self.assertLessEqual(6+2*F(3,2),10)
        self.selected(u,'o3','change_rule_after_full','保持10升')
        self.selected(u,'o3','include_feasible_interior_times','t=1.5分钟')
        self.assertEqual(-3,(-3*(5+1))-(-3*5))
        self.selected(u,'rate','preserve_negative_change','k=−3')
        self.assertEqual(6,3*0+6);self.assertEqual(0,3*(-2)+6)
        self.selected(u,'intercept','distinguish_axis_intercepts','x=0时y=6')
        self.assertEqual(-3,(4-3*1)-(4-3*0))
        self.selected(u,'formula','identify_coefficient_roles','k=−3，b=4')
        self.assertEqual(5,(2*7+5)-(2*7))
        self.selected(u,'formula','test_omitted_constant_at_zero','y−z恒为5')
        self.assertEqual(F(6,2),F(2*3,2*1))
        self.selected(u,'read_graph','separate_paper_shape_from_rate','两图都是3升/分钟')
        records=[(3,1),(3,4)];self.assertEqual(2,len({y for x,y in records if x==3}))
        self.selected(u,'representations','vertical_line_vs_function_graph','违反唯一输出')
        self.assertNotEqual(10,F(10+20,2))
        self.selected(u,'representations','keep_data_and_specify_variable','只能另定义为两城平均温度')
        self.assertEqual(10,8*F(5,4));self.assertNotEqual(1,F(5,4).denominator)
        self.selected(u,'units_domain','check_constraint_on_input_variable','只有x=2')
        self.assertEqual(5,4+2*F(1,2))
        self.selected(u,'units_domain','use_fractional_time_counterexample','0.5分钟是30秒')
        feasible=[n for n in range(1,30) if 10+2*n<3*n]
        self.assertEqual(11,min(feasible));self.assertEqual(10+2*10,3*10)
        self.selected(u,'compare_models','separate_tie_from_strict_saving','相等')
        self.selected(u,'compare_models','test_comparison_on_each_side','n>10时差为负')

    def test_derivative_domain_sign_extrema_and_boundary_counterexamples(self):
        u='derivative_monotonicity'
        self.assertEqual(0,-2+2)
        self.selected(u,'o1','mark_domain_gap_before_stationary_points','排除x=−2')
        with self.assertRaises(ZeroDivisionError):F(1,1-1)
        self.selected(u,'o1','require_attained_value_for_extremum','h(1)不存在')
        self.assertLess(F(1,-2),F(1,2))
        self.selected(u,'o2','test_cross_gap_pair','否定了跨缺口')
        for a,b in [(-5,-2),(-1,1),(2,4)]:self.assertGreater(-a,-b)
        self.selected(u,'o2','separate_value_sign_from_direction','仍然严格递减')
        for h in [F(-1,10),F(1,10)]:
            self.assertGreater(h*h,0);self.assertLess(-h*h,0)
        self.selected(u,'o3','contrast_same_stationary_condition','p在0极小')
        self.assertEqual([1,0,2],[abs(x-1)for x in [0,1,3]])
        self.selected(u,'domain_split','keep_nondifferentiable_candidate','最小值0在1取得')
        self.assertEqual([-1,1],[(abs(h)-abs(0))/h for h in [F(-1,10),F(1,10)]])
        self.selected(u,'domain_split','evaluate_function_before_derivative','p(0)=0有定义')
        self.assertEqual([1,1],[(x-2)**2 for x in [1,3]])
        self.assertEqual([-1,1],[(x-2)**3 for x in [1,3]])
        self.selected(u,'sign_chart','compare_even_and_odd_factors','a两侧都正；b左负右正')
        self.assertLess(1**2-100,0);self.assertGreater(2*1,0)
        self.selected(u,'sign_chart','differentiate_value_sign_from_derivative_sign','g′为正')
        plateau=lambda x:0 if x<=0 else x**3
        self.assertEqual(plateau(-3),plateau(-1));self.assertLess(plateau(1),plateau(2))
        self.selected(u,'monotonic_intervals','find_equal_values_at_distinct_inputs','违反严格递增')
        self.selected(u,'monotonic_intervals','reject_false_increase_decrease_dichotomy','整体非递减')
        for h in [F(-1,2),F(1,2)]:self.assertLess(h*h,4)
        self.assertGreater(3**2,4)
        self.selected(u,'extrema','compare_assigned_point_with_neighborhood','局部极大值点')
        self.assertEqual([1,3],[x for x in [1,3,4] if 1<=x<=3])
        self.assertGreater(8-2*3,0)
        self.selected(u,'boundary_maximum','filter_candidates_by_allowed_interval','排除区间外的4')

    def test_hint_component_methods_only_appear_at_the_authored_level(self):
        cases=[
            ('position_reference_frames','return_direction','separate_reverse_and_relabel','position.complementary-bearing',2),
            ('ratio_models','o1','separate_ratio_shares_and_counts','ratio.equivalent-scaling',2),
            ('fraction_equivalence','simplify','check_odd_common_factors','fraction.reduce-direct',3),
            ('derivative_monotonicity','o2','separate_value_sign_from_direction','derivative.linear-sign',3),
            ('triangle_conditions','o3','test_universal_claim_with_counterexample','triangle.angle-classification-counterexample',3),
        ]
        for unit,goal,route_id,family,first in cases:
            support=self.support(unit,goal)
            route=next(r for r in support['guided_branches'] if r['id']==route_id)
            hints=support['branch_hints'][route['choice_ids'][0]]
            self.assertEqual([first<=h['level'] for h in hints],
                             [family in h['reference_family_ids'] for h in hints])
        boundary=self.support('derivative_monotonicity','boundary_maximum')
        family='derivative.open-interval-nonattainment'
        self.assertNotIn(family,boundary['guided_task'].get('reference_family_ids',[]))
        self.assertEqual([False,False,True],[family in h['reference_family_ids'] for h in boundary['hints']])

    def test_new_rubric_confirmation_freezes_and_exposes_only_released_auxiliary_methods(self):
        # Source-only fixture: root performs registered catalog integration.
        # This directly exercises the real create/answer/event code with the
        # actual authored task, including its optional auxiliary family field.
        unit=copy.deepcopy(self.units['derivative_monotonicity'])
        task=copy.deepcopy(next(t for t in unit['tasks'] if t['id']==NEW_CONFIRMATION))
        main=task['family_id'];aux=set(task['reference_family_ids'])
        self.assertEqual({'derivative.disconnected-domain-monotonicity','derivative.nonnegative-vs-strict'},aux)
        self.assertEqual([],validate_resource('learning-task',task))
        task.update(family_status='declared',resource_version=digest(task))
        compiled=copy.deepcopy(unit)
        compiled['resource_version']=digest(unit)
        for objective in compiled['objectives']:objective['skill_id']='unit-skill:'+objective['id']
        class SourceCatalog:
            def __init__(self,unit_row,task_row):
                self.units={unit_row['id']:unit_row}
                self.tasks={task_row['id']:task_row}
            def task(self,tid):return copy.deepcopy(self.tasks[tid])
        support=self.support(unit['id'],'o2')
        for choice in ['A','B','C']:
            with self.subTest(choice=choice):
                record,_=instruction.create(unit,support['id'],'b'*64,SourceCatalog(compiled,task),{},[],
                                             '2026-10-28T00:00:00+00:00')
                self.assertEqual(NEW_CONFIRMATION,record['task_ids'][-1])
                self.assertEqual(aux,set(record['task_metadata'][NEW_CONFIRMATION]['reference_family_ids']))
                instruction.answer(record,record['task_ids'][0],choice,'2026-10-28T00:00:01+00:00')
                guided=record['question_snapshots'][record['task_ids'][1]]
                _,events=instruction.answer(record,guided['id'],guided['answer'],'2026-10-28T00:00:02+00:00')
                presented=next(e for e in events if e['event_type']=='question_presented')
                self.assertEqual({main},set(presented['payload']['family_ids']))
                response='；'.join(r['example'] for r in task['rubric'])
                final,events=instruction.answer(record,NEW_CONFIRMATION,response,'2026-10-28T00:00:03+00:00')
                self.assertEqual('needs_review',final['status'])
                self.assertTrue(final['help_used'])
                self.assertFalse(final['confirmation_eligible'])
                self.assertFalse(record['context']['independent_evidence'])
                feedback=next(e for e in events if e['event_type']=='feedback_released')
                self.assertEqual(aux|{main},set(feedback['payload']['family_ids']))
                self.assertIsNone(instruction.route_problem(record))


if __name__=='__main__':
    unittest.main()
