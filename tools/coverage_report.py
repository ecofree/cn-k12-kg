"""分开统计教材导航、教学材料、细技能任务与独立证据；缺口不伪装为错误。

--check 只检查结构、引用与分母一致性，真实缺口和待审核不导致失败。
--json 输出包括逐节点缺口与逐活动工作清单的机器可读报告。
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cnkg.learning import load_units, LearningUnitError  # noqa: E402
from cnkg.projects import load_projects, ProjectError, outline_activities  # noqa: E402
from tools.teach_cards import check_card  # noqa: E402


def _read(data, name):
    return json.loads((data / name).read_text(encoding='utf-8'))


def stage_of(topic):
    grade = topic.get('grade', 0)
    return 'primary' if grade <= 6 else ('junior' if grade <= 9 else 'senior')


def check_version_registry(registry, standards):
    errors = []
    if not isinstance(registry, dict) or registry.get('schema') != 1:
        return ['curriculum_versions.schema 必须为 1']
    versions = registry.get('versions')
    if not isinstance(versions, list) or not versions:
        return ['curriculum_versions.versions 不能为空']
    ids = [v.get('id') for v in versions if isinstance(v, dict)]
    if len(ids) != len(versions) or any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids)):
        return ['curriculum_versions.versions ID 无效或重复']
    index = {v['id']: v for v in versions}
    for version in versions:
        if version.get('full_text_status') == 'acquired' and not version.get('file_sha256'):
            errors.append(f"{version['id']}: 取得全文必须登记文件哈希")
        if version.get('comparison_status') == 'complete' and version.get('full_text_status') != 'acquired':
            errors.append(f"{version['id']}: 未取得全文不能声明差异核对完成")
    work = registry.get('clause_worklist')
    if not isinstance(work, list):
        return errors + ['curriculum_versions.clause_worklist 必须为列表']
    seen = set()
    for row in work:
        if not isinstance(row, dict) or row.get('standard_id') not in standards:
            errors.append('版本差异工作项引用不存在课标')
            continue
        sid = row['standard_id']
        if sid in seen:
            errors.append(f'重复版本差异工作项 {sid}')
        seen.add(sid)
        for field in ('from_version', 'to_version'):
            if row.get(field) not in index:
                errors.append(f'{sid}: {field} 无效')
        old = standards[sid].get('verbatim_text', '').replace('\r\n', '\n').replace('\r', '\n')
        if row.get('baseline_excerpt_sha256') != hashlib.sha256(old.encode('utf-8')).hexdigest():
            errors.append(f'{sid}: 基线原文摘录已变，需重核版本工作项')
        status = row.get('status')
        if status not in ('pending_source', 'pending_comparison', 'pending_review', 'reviewed'):
            errors.append(f'{sid}: 差异工作项状态无效')
        if status == 'reviewed':
            review = row.get('review') or {}
            target = index.get(row.get('to_version'), {})
            if (target.get('full_text_status') != 'acquired'
                    or not row.get('target_locator') or not row.get('change_class')
                    or review.get('reviewer_type') not in ('teacher', 'subject_expert')
                    or not review.get('reviewer_id') or not review.get('record_id')):
                errors.append(f'{sid}: 已核对须有新版原文定位、差异分类和独立审核记录')
    for sid in sorted(set(standards) - seen):
        errors.append(f'缺少版本工作项 {sid}')
    return errors


def build_report(data_dir=None):
    data = Path(data_dir or ROOT / 'data')
    topics_list = _read(data, 'topics.json')
    topics = {t['id']: t for t in topics_list}
    standards = {s['id']: s for s in _read(data, 'standards.json')}
    outline = _read(data, 'outline.json')
    tiers = _read(data, 'topic_tiers.json')['topics']
    bank = {q['id']: q for q in _read(data, 'questions.json')}
    errors = []
    if len(topics) != len(topics_list):
        errors.append('教材节点 ID 重复')
    if set(tiers) != set(topics):
        errors.append('分层登记与教材节点集合不一致')
    if any(i.get('tier') not in ('core', 'tool') for i in tiers.values()):
        errors.append('存在非 core/tool 的分层标签')
    cards = {}
    valid_cards = set()
    for path in sorted((data / 'teach').glob('*.json')):
        card = json.loads(path.read_text(encoding='utf-8'))
        tid = card.get('topicId')
        if tid in cards:
            errors.append(f'完整卡重复 {tid}')
        cards[tid] = card
        card_errors = check_card(card, topics, bank)
        errors.extend(f'{path.name}: {e}' for e in card_errors)
        if not card_errors:
            valid_cards.add(tid)
    briefs = [json.loads(p.read_text(encoding='utf-8')) for p in sorted((data / 'teach' / 'clusters').glob('*.json'))]
    reference_ids = {tid for c in briefs for tid in c.get('covers', [])}
    if reference_ids - set(topics):
        errors.append('速查卡存在未知教材节点')
    try:
        units = load_units(data)
    except LearningUnitError as exc:
        units = {}
        errors.extend(exc.errors)
    try:
        projects = load_projects(data)
    except ProjectError as exc:
        projects = {}
        errors.extend(exc.errors)
    registry = _read(data, 'curriculum_versions.json')
    errors.extend(check_version_registry(registry, standards))
    backlog = _read(data, 'coverage_backlog.json') if (data / 'coverage_backlog.json').exists() else {'items': []}
    app_open = []
    for item in backlog.get('items', []):
        if item.get('topic_id') not in topics:
            errors.append(f"应用任务缺口引用不存在节点 {item.get('topic_id')}")
        if item.get('status') == 'open':
            app_open.append(item)
    if len({x['topic_id'] for x in app_open}) != len(app_open):
        errors.append('应用任务待办含重复节点')
    core = {tid for tid, info in tiers.items() if info.get('tier') == 'core'}
    tool = {tid for tid, info in tiers.items() if info.get('tier') == 'tool'}
    associated_units = defaultdict(list)
    for uid, unit in units.items():
        for tid in unit['topic_ids']:
            associated_units[tid].append(uid)
    topic_rows = []
    for tid, topic in topics.items():
        topic_rows.append({'id': tid, 'title': topic['name'], 'stage': stage_of(topic),
                           'tier': tiers.get(tid, {}).get('tier'),
                           'legacy_full_card': tid in valid_cards,
                           'reference_brief': tid in reference_ids,
                           'learning_unit_ids': sorted(associated_units[tid]),
                           'full_objective_coverage': 'not_audited',
                           'core_full_card_gap': tid in core and tid not in valid_cards})
    activities = outline_activities(outline)
    project_refs = defaultdict(list)
    for pid, project in projects.items():
        for ref in project['outline_refs']:
            project_refs[(ref['book_id'], ref['title'])].append(pid)
    activity_rows = [{**a, 'project_ids': sorted(project_refs[(a['book_id'], a['title'])]),
                      'status': 'draft_resource' if project_refs[(a['book_id'], a['title'])] else 'pending_development'}
                     for a in activities]
    tasks = [t for u in units.values() for t in u['tasks']]
    objectives = []
    for uid, unit in units.items():
        for obj in unit['objectives']:
            linked = [t for t in unit['tasks'] if obj['id'] in t['objective_ids']]
            objectives.append({'unit_id': uid, **obj, 'task_ids': [t['id'] for t in linked],
                               'phases': sorted({t['phase'] for t in linked}),
                               'alignment_status': 'author_mapping_pending_teacher_review'})
    by_stage = {}
    for stage in ('primary', 'junior', 'senior'):
        ids = {tid for tid, t in topics.items() if stage_of(t) == stage}
        by_stage[stage] = {'navigation_topics': len(ids), 'core_topics': len(ids & core),
                           'legacy_full_cards': len(ids & valid_cards),
                           'core_full_card_gaps': len((ids & core) - valid_cards)}
    pending_versions = sum(w.get('status') != 'reviewed' for w in registry.get('clause_worklist', []))
    return {
        'schema': 1, 'ok': not errors, 'errors': errors,
        'scope': '资源计数与结构校验；关联一个单元不表示覆盖教材节点的全部目标；未进行全课程细技能穷举。',
        'navigation': {'volumes': len(outline['volumes']), 'topics': len(topics),
                       'standards': len(standards), 'core_topics': len(core), 'tool_topics': len(tool)},
        'explanations': {'legacy_full_cards': len(valid_cards), 'topic_denominator': len(topics),
                         'core_with_full_card': len(core & valid_cards), 'core_denominator': len(core),
                         'core_full_card_gaps': len(core - valid_cards),
                         'brief_cards': len(briefs), 'brief_reference_topics': len(reference_ids),
                         'brief_reference_is_full_coverage': False},
        'fine_skills': {'learning_units': len(units), 'objectives': len(objectives), 'tasks': len(tasks),
                        'task_phases': dict(Counter(t['phase'] for t in tasks)),
                        'task_types': dict(Counter(t['type'] for t in tasks)),
                        'supplement_units': sum(u.get('coverage_kind') == 'curriculum_supplement' for u in units.values()),
                        'topic_navigation_associations': len({t for u in units.values() for t in u['topic_ids']}),
                        'whole_curriculum_objective_denominator': None,
                        'whole_curriculum_coverage_rate': None,
                        'explanation': '细技能总分母尚未逐条拆解；不能用目录节点数作目标覆盖率分母。'},
        'activities': {'outline_count': len(activities), 'draft_materials': len(projects),
                       'with_materials': sum(bool(a['project_ids']) for a in activity_rows),
                       'pending_development': sum(not a['project_ids'] for a in activity_rows),
                       'declared_junior_hours': sum(a['hours'] or 0 for a in activities if a['stage'] == 'junior'),
                       'teacher_review_complete': sum(p['teacher_review']['status'] == 'approved' for p in projects.values())},
        'teacher_review': {
            'learning_units': {'verified_approved': sum(u['teacher_review']['status'] == 'approved' for u in units.values()), 'denominator': len(units)},
            'projects': {'verified_approved': sum(p['teacher_review']['status'] == 'approved' for p in projects.values()), 'denominator': len(projects)},
            'legacy_full_cards': {'verified_approved': None, 'denominator': len(valid_cards), 'status': 'version_bound_reviews_not_audited'},
            'scope': '单元与项目仅计通过身份及内容哈希检查的批准记录；结构检查、算式复算和 AI 复核不计教师审核。'},
        'student_evidence': {'status': 'not_collected_or_not_audited', 'verified_participants': None,
                             'educational_effect': 'pending',
                             'scope': '匿名使用日志或演示会话不自动构成真实学生试点；独立迁移、实际延迟与教师评价须另行审核。'},
        'curriculum_versions': {'pending_clause_comparisons': pending_versions,
                                'clause_denominator': len(standards),
                                'status': 'pending' if pending_versions else 'reviewed'},
        'application_task_backlog': {'open_count': len(app_open), 'core_denominator': len(core),
                                      'items': app_open},
        'by_stage': by_stage, 'topic_worklist': topic_rows,
        'objective_worklist': objectives, 'activity_worklist': activity_rows,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--output', type=Path, help='另存 JSON 报告')
    args = parser.parse_args(argv)
    try:
        report = build_report(args.data_dir)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report = {'ok': False, 'errors': [f'无法完成覆盖统计：{exc}']}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif not report['ok']:
        print('覆盖结构检查失败：\n' + '\n'.join(report['errors']))
    else:
        n, e, f, a = (report[k] for k in ('navigation', 'explanations', 'fine_skills', 'activities'))
        print(f"教材导航 {n['topics']} 点；core {n['core_topics']} / tool {n['tool_topics']}。")
        print(f"既有完整卡 {e['legacy_full_cards']}/{e['topic_denominator']}；core 完整卡 {e['core_with_full_card']}/{e['core_denominator']}，缺口 {e['core_full_card_gaps']}。")
        print(f"独立单元 {f['learning_units']} 个，细目标 {f['objectives']} 个，任务 {f['tasks']} 道；全课程目标分母尚未确定。")
        print(f"综合实践已有材料 {a['with_materials']}/{a['outline_count']}，待开发 {a['pending_development']}。")
        print(f"应用任务待办 {report['application_task_backlog']['open_count']}；课标逐条差异待核对 {report['curriculum_versions']['pending_clause_comparisons']}。")
        print('PASS 仅表示结构和引用可用；教师审核、真实学生证据及教育效果仍分别等待核验。')
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
