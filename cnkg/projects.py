"""原创综合实践材料、目录引用及版本绑定的人工审核。

结构通过只表明任务和评价目标有显式联系，不表示教师认可或教育效果。
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
from pathlib import Path
import re

DATA_DIR = Path(__file__).resolve().parents[1] / 'data'
_ID = re.compile(r'[a-z][a-z0-9_:-]{0,127}\Z')


class ProjectError(ValueError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__('综合实践校验失败：\n' + '\n'.join(self.errors))


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _texts(value, nonempty=True):
    return isinstance(value, list) and (bool(value) or not nonempty) and all(_text(x) for x in value)


def outline_activities(outline):
    """按册次和原题名登记；活动与项目均保留原目录类别及已知课时。"""
    rows = []
    for volume in outline.get('volumes', []):
        for kind in ('activities', 'projects'):
            for activity in volume.get(kind) or []:
                rows.append({'book_id': volume['volume'], 'title': activity['title'],
                             'stage': volume['stage'], 'kind': kind,
                             'hours': activity.get('hours'), 'page': activity.get('page')})
    return rows


def project_content_sha256(project):
    content = {k: v for k, v in project.items()
               if k not in {'teacher_review', 'review_status', 'content_sha256'}}
    payload = json.dumps(content, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def check_project(project, outline=None, standards=None):
    if not isinstance(project, dict):
        return ['project 必须是对象']
    errors = []
    if project.get('schema') != 1:
        errors.append('schema 必须为 1')
    if not _text(project.get('id')) or not _ID.fullmatch(project['id']):
        errors.append('id 必须为稳定的小写英文标识')
    for key in ('title', 'task_brief', 'duration_hint', 'assessment_note', 'standard_alignment_note'):
        if not _text(project.get(key)):
            errors.append(f'{key} 不能为空')
    stage = project.get('stage')
    if stage not in ('primary', 'junior', 'senior'):
        errors.append('stage 必须为 primary/junior/senior')
    if project.get('author_type') not in ('ai', 'human', 'mixed'):
        errors.append('author_type 必须明确作者类型')
    if project.get('review_status') not in ('draft', 'reviewed', 'rejected'):
        errors.append('review_status 必须明确')
    for key in ('materials', 'deliverables'):
        if not _texts(project.get(key)):
            errors.append(f'{key} 必须有具体材料/交付物')
    refs = project.get('outline_refs')
    known = {(a['book_id'], a['title']): a for a in outline_activities(outline)} if outline is not None else None
    seen = set()
    supplement = project.get('coverage_kind') == 'curriculum_supplement'
    if project.get('coverage_kind', 'outline_activity') not in ('outline_activity', 'curriculum_supplement'):
        errors.append('coverage_kind 必须明确为目录活动或课程补充')
    if supplement:
        if refs != [] or not _texts(project.get('topic_ids')):
            errors.append('课程补充须提供真实 topic_ids，outline_refs 应为空，不能冒充教材原活动')
        provenance = project.get('provenance', {})
        if provenance.get('outline_used_for') != 'topic_navigation_only':
            errors.append('课程补充须明确目录仅用于主题导航')
    if not isinstance(refs, list) or (not refs and not supplement):
        errors.append('outline_refs 必须关联原目录的实际活动')
    else:
        for ref in refs:
            if not isinstance(ref, dict) or not all(_text(ref.get(k)) for k in ('book_id', 'title')):
                errors.append('outline_refs 每项需要 book_id 和 title')
                continue
            key = (ref['book_id'], ref['title'])
            if key in seen:
                errors.append('outline_refs 引用重复')
            seen.add(key)
            if known is not None:
                if key not in known:
                    errors.append(f'目录没有活动 {key}')
                elif known[key]['stage'] != stage:
                    errors.append(f'活动 {key} 学段不符')
    refs = project.get('standard_ids')
    if not _texts(refs) or len(refs) != len(set(refs)):
        errors.append('standard_ids 必须是无重复的非空 ID 列表')
    elif standards is not None:
        ids = set(standards) if isinstance(standards, dict) else {s if isinstance(s, str) else s['id'] for s in standards}
        for ref in refs:
            if ref not in ids:
                errors.append(f'不存在课标引用 {ref}')
    objectives = project.get('objectives')
    objective_ids = set()
    if not isinstance(objectives, list) or not objectives:
        errors.append('objectives 不能为空')
    else:
        for objective in objectives:
            if (not isinstance(objective, dict) or not _text(objective.get('id'))
                    or not _ID.fullmatch(objective['id']) or not _text(objective.get('statement'))):
                errors.append('每个目标必须有稳定 ID 与可观察 statement')
                continue
            if objective['id'] in objective_ids:
                errors.append('objectives ID 重复')
            objective_ids.add(objective['id'])
    covered = {'steps': set(), 'rubric': set()}
    for kind in ('steps', 'rubric'):
        rows = project.get(kind)
        if not isinstance(rows, list) or not rows:
            errors.append(f'{kind} 不能为空')
            continue
        for i, row in enumerate(rows):
            prefix = f'{kind}[{i}]'
            if not isinstance(row, dict):
                errors.append(f'{prefix} 必须为对象')
                continue
            keys = ('title', 'instructions', 'deliverable') if kind == 'steps' else ('criterion', 'example')
            for key in keys:
                if not _text(row.get(key)):
                    errors.append(f'{prefix}.{key} 不能为空')
            refs = row.get('objective_ids')
            if not _texts(refs) or len(refs) != len(set(refs)):
                errors.append(f'{prefix}.objective_ids 必须明确且无重复')
            else:
                for ref in refs:
                    if ref not in objective_ids:
                        errors.append(f'{prefix} 引用未知目标 {ref}')
                    else:
                        covered[kind].add(ref)
            if kind == 'rubric':
                pts = row.get('points')
                try:
                    valid = not isinstance(pts, bool) and isinstance(pts, (int, float)) and math.isfinite(pts) and pts > 0
                except OverflowError:
                    valid = False
                if not valid:
                    errors.append(f'{prefix}.points 必须为有限正数')
    for kind, ids in covered.items():
        for missing in sorted(objective_ids - ids):
            errors.append(f'目标 {missing} 缺少 {kind} 证据')
    review = project.get('teacher_review')
    if not isinstance(review, dict) or review.get('status') not in ('pending', 'approved', 'rejected'):
        errors.append('teacher_review.status 必须明确')
    elif review['status'] == 'approved':
        if (review.get('reviewer_type') not in ('teacher', 'subject_expert')
                or not _text(review.get('reviewer_id')) or not _text(review.get('record_id'))):
            errors.append('教师审核须有独立身份和记录')
        try:
            if review.get('content_sha256') != project_content_sha256(project):
                errors.append('教师审核未绑定当前内容哈希')
        except (TypeError, ValueError):
            errors.append('内容无法计算哈希')
    if project.get('review_status') == 'reviewed' and (not isinstance(review, dict) or review.get('status') != 'approved'):
        errors.append('reviewed 不能替代有效教师审核')
    return errors


def load_projects(data_dir=None):
    data = Path(data_dir or DATA_DIR)
    directory = data / 'projects'
    if not directory.exists():
        return {}
    try:
        outline = json.loads((data / 'outline.json').read_text(encoding='utf-8'))
        standards = json.loads((data / 'standards.json').read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectError([f'无法加载引用表：{exc}']) from exc
    projects, errors = {}, []
    for path in sorted(directory.glob('*.json')):
        try:
            project = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f'{path.name}: {exc}')
            continue
        problems = check_project(project, outline, standards)
        if project.get('coverage_kind') == 'curriculum_supplement':
            topics = {t['id'] for t in json.loads((data / 'topics.json').read_text(encoding='utf-8'))}
            problems.extend(f'不存在教材主题 {tid}' for tid in project.get('topic_ids', []) if tid not in topics)
        errors.extend(f'{path.name}: {e}' for e in problems)
        if problems:
            continue
        if project['id'] in projects:
            errors.append(f'{path.name}: 项目 ID 重复')
        projects[project['id']] = project
    if errors:
        raise ProjectError(errors)
    return projects


def public_project(project):
    """公开任务和评价标准，隐藏评价示例与教师参考结果；无自动掌握结论。"""
    keys = ('id', 'title', 'stage', 'outline_refs', 'standard_ids', 'standard_alignment_note',
            'duration_hint', 'task_brief', 'objectives', 'materials', 'steps', 'deliverables',
            'assessment_note', 'review_status', 'author_type')
    shown = {k: copy.deepcopy(project[k]) for k in keys}
    shown['rubric'] = [{k: copy.deepcopy(row[k]) for k in ('criterion', 'points', 'objective_ids')}
                       for row in project['rubric']]
    shown['teacher_review'] = {'status': project['teacher_review']['status']}
    shown['content_sha256'] = project_content_sha256(project)
    return shown


def project_summary(project):
    return {
        'id': project['id'], 'title': project['title'], 'stage': project['stage'],
        'duration_hint': project['duration_hint'],
        'objective_count': len(project['objectives']),
        'outline_refs': copy.deepcopy(project['outline_refs']),
        'review_status': project['review_status'],
        'teacher_review': {'status': project['teacher_review']['status']},
        'content_sha256': project_content_sha256(project),
    }
