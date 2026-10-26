"""登记已完成的条款对照，并校验报告引用的原始比较记录。

本工具不会读取一个版本题名就推断差异，也不将 AI 对照升级为教师审核。
比较正文保存在 data/curriculum_comparisons；本工具仅同步和验证登记信息。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def excerpt_hash(standard):
    text = standard.get('verbatim_text', '').replace('\r\n', '\n').replace('\r', '\n')
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def check_comparison_records(data, registry, standards):
    """完成状态必须与具体文件内同一条款的哈希、定位、分类和结论吻合。"""
    data = Path(data)
    versions = {v['id']: v for v in registry.get('versions', [])}
    errors, cache = [], {}
    for row in registry.get('clause_worklist', []):
        if row.get('comparison_status') != 'complete':
            continue
        sid = row.get('standard_id')
        comparison = row.get('comparison') or {}
        name = comparison.get('record_path', '')
        path = Path(name)
        if (len(path.parts) != 3 or path.parts[:2] != ('data', 'curriculum_comparisons')
                or path.suffix != '.json'):
            errors.append(f'{sid}: 比较记录路径必须位于 data/curriculum_comparisons')
            continue
        local = data / 'curriculum_comparisons' / path.name
        if name not in cache:
            try:
                capsule = json.loads(local.read_text(encoding='utf-8'))
                if not isinstance(capsule, dict) or capsule.get('schema') != 1:
                    raise ValueError('schema 必须为 1')
                clauses = capsule.get('clauses', [])
                if (not isinstance(clauses, list) or not clauses
                        or not all(isinstance(c, dict) and isinstance(c.get('standard_id'), str) for c in clauses)):
                    raise ValueError('条款列表无效')
                ids = [c['standard_id'] for c in clauses]
                if len(ids) != len(set(ids)):
                    raise ValueError('重复条款')
                scope = capsule.get('scope') or {}
                if scope.get('registered_clauses') != len(clauses) or scope.get('completed_clauses') != len(clauses):
                    raise ValueError('条款分母与完成数不符')
                if capsule.get('comparison_status') != 'complete':
                    raise ValueError('比较记录尚未完成')
                cache[name] = (capsule, {c['standard_id']: c for c in clauses})
            except (OSError, ValueError, TypeError) as exc:
                errors.append(f'{sid}: 比较记录无法读取或无效：{exc}')
                cache[name] = None
        if cache[name] is None:
            continue
        capsule, clauses = cache[name]
        record = clauses.get(sid)
        if record is None:
            errors.append(f'{sid}: 比较记录中缺少同一条款')
            continue
        source = capsule.get('source_document') or {}
        target = versions.get(row.get('to_version'), {})
        digest = source.get('file_sha256')
        if (not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
                or digest != target.get('file_sha256')
                or digest != comparison.get('target_file_sha256')
                or digest != record.get('target_file_sha256')):
            errors.append(f'{sid}: 比较记录的新版文件哈希不一致')
        if (sid not in standards or record.get('baseline_excerpt_sha256') != excerpt_hash(standards.get(sid, {}))
                or record.get('baseline_excerpt_sha256') != row.get('baseline_excerpt_sha256')):
            errors.append(f'{sid}: 比较记录的基线摘录哈希不一致')
        for key in ('from_version', 'to_version', 'target_locator', 'change_class'):
            if record.get(key) != row.get(key):
                errors.append(f'{sid}: 比较记录与登记的 {key} 不一致')
        if (record.get('summary') != comparison.get('summary') or not record.get('summary')
                or record.get('comparison_status') != 'complete'):
            errors.append(f'{sid}: 比较结论缺失或未与记录绑定')
        if capsule.get('to_version') != row.get('to_version') or capsule.get('from_version') != row.get('from_version'):
            errors.append(f'{sid}: 比较文件的版本范围不一致')
        max_page = source.get('pdf_pages')
        locators = record.get('target_locator')
        if not isinstance(locators, list) or not locators:
            errors.append(f'{sid}: 比较记录缺少新版页码')
            continue
        for loc in locators:
            pages = loc.get('pdf_pages_one_based') if isinstance(loc, dict) else None
            if (not isinstance(max_page, int) or not isinstance(pages, list) or not pages
                    or any(type(p) is not int or not 1 <= p <= max_page for p in pages)
                    or pages != sorted(set(pages)) or not loc.get('section') or not loc.get('paragraph_scope')):
                errors.append(f'{sid}: 比较记录页码或段落定位无效')
    return errors


def synchronize(data):
    data = Path(data)
    registry = json.loads((data / 'curriculum_versions.json').read_text(encoding='utf-8'))
    standards = {s['id']: s for s in json.loads((data / 'standards.json').read_text(encoding='utf-8'))}
    versions = {v['id']: v for v in registry['versions']}
    work = {r['standard_id']: r for r in registry['clause_worklist']}
    seen = set()
    for path in sorted((data / 'curriculum_comparisons').glob('*.json')):
        capsule = json.loads(path.read_text(encoding='utf-8'))
        source = capsule['source_document']
        version = versions[capsule['to_version']]
        version.update(full_text_status='acquired', file_sha256=source['file_sha256'],
                       pdf_pages=source['pdf_pages'], file_size_bytes=source['file_size_bytes'],
                       comparison_status='registered_clauses_compared', checked_at=capsule['completed_at'],
                       comparison_record_path=f'data/curriculum_comparisons/{path.name}',
                       comparison_scope=capsule['scope'])
        version['sources'] = [{'url': source['source_url'], 'source_type': source['source_type'],
                               'evidence_scope': source['source_verification']}]
        if source.get('institutional_listing_url'):
            version['sources'].append({'url': source['institutional_listing_url'],
                                       'publisher': source['institutional_listing_publisher'],
                                       'published_at': source['institutional_listing_published_at'],
                                       'source_type': 'institutional_resource_listing',
                                       'evidence_scope': '机构页面用于核对版本题名；实际读取的全文和哈希见镜像记录。'})
        for record in capsule['clauses']:
            sid = record['standard_id']
            if sid in seen or sid not in work:
                raise ValueError(f'比较记录条款重复或未登记：{sid}')
            seen.add(sid)
            row = work[sid]
            if row.get('status') == 'reviewed':
                raise ValueError(f'{sid}: 已有独立审核，需人工核实版本后更新，不自动覆盖审核记录')
            row.update(status='pending_review', comparison_status='complete',
                       target_locator=record['target_locator'], change_class=record['change_class'],
                       comparison={'author_type': capsule['reviewer_type'], 'completed_at': capsule['completed_at'],
                                   'summary': record['summary'], 'record_path': f'data/curriculum_comparisons/{path.name}',
                                   'target_file_sha256': source['file_sha256']},
                       review={'status': 'pending', 'deferred_by_user': True},
                       next_action='独立教师审核按用户要求暂缓；条款对照与对应材料修订已完成。')
    registry['whole_standard_coverage'] = ('78项已登记摘录已完成2025版对照；此分母不等同于整部课标全部文字或全部细技能。'
                                         '保留原摘录和ID，新版定位、差异与教学跟进另存比较记录；不宣称完成全文逐字旧新认证。')
    errors = check_comparison_records(data, registry, standards)
    if errors:
        raise ValueError('\n'.join(errors))
    (data / 'curriculum_versions.json').write_text(json.dumps(registry, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return len(seen)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write', action='store_true', help='从已完成的比较记录同步登记表')
    args = parser.parse_args()
    data = ROOT / 'data'
    if args.write:
        print(f'已登记 {synchronize(data)} 项条款对照；独立审核状态未升级。')
    else:
        registry = json.loads((data / 'curriculum_versions.json').read_text(encoding='utf-8'))
        standards = {s['id']: s for s in json.loads((data / 'standards.json').read_text(encoding='utf-8'))}
        errors = check_comparison_records(data, registry, standards)
        for error in errors:
            print(error)
        print(f'比较记录校验：{len(errors)} 个错误')
        return bool(errors)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
