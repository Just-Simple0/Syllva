"""Notion data-source reader with scoped queries and no mutation methods."""

from __future__ import annotations

from typing import Any

from uls.adapters.notion.base import normalize_alias, parse_aliases
from uls.domain.errors import ProviderUnavailableError, SourcePartialError, SourceUnavailableError

_RELATION_DATABASES = {
    'Session': 'sessions', 'Sessions': 'sessions', 'Included Sessions': 'sessions',
    'Related Sessions': 'sessions', 'Material': 'materials', 'Materials': 'materials',
    'Related Materials': 'materials',
}


def _plain(value: Any) -> str:
    if not isinstance(value, list):
        raise SourceUnavailableError('Notion rich text is malformed')
    return ''.join(item.get('plain_text', item.get('text', {}).get('content', '')) for item in value)


class NotionAPIReader:
    def __init__(self, client: Any, config: Any, *, max_records: int = 1000) -> None:
        self._database = client.databases.retrieve
        self._query = client.data_sources.query
        self._page = client.pages.retrieve
        self._children = client.blocks.children.list
        self._cfg = config
        self._sources: dict[str, str] = {}
        self.max_records = max_records

    def _call(self, method: Any, **kwargs: Any) -> Any:
        try:
            return method(**kwargs)
        except Exception:  # noqa: BLE001 - redact provider failures at the adapter boundary
            raise ProviderUnavailableError('Notion read failed') from None

    def _data_source(self, kind: str) -> str:
        if kind not in self._sources:
            database_id = getattr(self._cfg, kind + '_db_id')
            result = self._call(self._database, database_id=database_id)
            sources = result.get('data_sources', [])
            if len(sources) != 1 or not sources[0].get('id'):
                raise SourceUnavailableError('Configured Notion database must have exactly one data source')
            self._sources[kind] = sources[0]['id']
        return self._sources[kind]

    def _check_parent(self, page: Any, kind: str) -> None:
        parent = page.get('parent', {})
        if (parent.get('data_source_id', '').replace('-', '') != self._data_source(kind).replace('-', '')
                or page.get('archived') is not False or page.get('in_trash', False) is not False):
            raise SourceUnavailableError('Notion page is outside its configured data source or is archived')

    def _rows(self, kind: str, filter_value: Any = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor = None
        seen = set()
        while True:
            kwargs: dict[str, Any] = {'data_source_id': self._data_source(kind), 'page_size': 100}
            if filter_value:
                kwargs['filter'] = filter_value
            if cursor:
                kwargs['start_cursor'] = cursor
            result = self._call(self._query, **kwargs)
            pages = result.get('results')
            if not isinstance(pages, list):
                raise SourceUnavailableError('Notion query is malformed')
            for page in pages:
                self._check_parent(page, kind)
                rows.append(self._normalize(page))
                if len(rows) > self.max_records:
                    raise SourcePartialError('Notion catalog exceeds bounded read limit')
            if result.get('has_more') is False:
                return rows
            cursor = result.get('next_cursor')
            if not cursor or cursor in seen:
                raise SourcePartialError('Notion pagination is incomplete')
            seen.add(cursor)

    def _normalize(self, page: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {'id': page['id']}
        for name, prop in page.get('properties', {}).items():
            kind = prop.get('type')
            value = prop.get(kind)
            if kind in {'title', 'rich_text'}:
                result[name] = _plain(value)
            elif kind in {'select', 'status'}:
                result[name] = value.get('name') if isinstance(value, dict) else None
            elif kind == 'multi_select':
                result[name] = [item['name'] for item in value]
            elif kind == 'relation':
                if prop.get('has_more', False) is not False or not isinstance(value, list):
                    raise SourcePartialError('Notion relation is truncated')
                if name in _RELATION_DATABASES:
                    ids = []
                    for relation in value:
                        related = self._call(self._page, page_id=relation['id'])
                        self._check_parent(related, _RELATION_DATABASES[name])
                        entity_id = _plain(related['properties']['ID']['rich_text'])
                        ids.append({'id': entity_id})
                    result[name] = {'relation': ids}
                else:
                    result[name] = {'relation': value}
            elif kind in {'number', 'checkbox', 'url', 'date'}:
                result[name] = value
        return result

    def _get(self, kind: str, entity_id: str) -> dict[str, Any] | None:
        rows = self._rows(kind, {'property': 'ID', 'rich_text': {'equals': entity_id}})
        if len(rows) > 1 or any(row.get('ID') != entity_id for row in rows):
            raise SourceUnavailableError('Notion entity identity is ambiguous')
        return rows[0] if rows else None

    def get_session(self, entity_id: str) -> Any:
        return self._get('sessions', entity_id)

    def get_material(self, material_id: str) -> Any:
        return self._get('materials', material_id)

    def get_exam(self, exam_id: str) -> Any:
        return self._get('exams', exam_id)

    def get_activity(self, activity_id: str) -> Any:
        return self._get('activities', activity_id)

    def get_course_by_relation_id(self, relation_page_id: str) -> Any:
        page = self._call(self._page, page_id=relation_page_id)
        self._check_parent(page, 'courses')
        if page['id'].replace('-', '') != relation_page_id.replace('-', ''):
            raise SourceUnavailableError('Course page identity mismatch')
        return self._normalize(page)

    def get_course_by_alias(self, alias_norm: str) -> Any:
        wanted = normalize_alias(alias_norm)
        matches = [row for row in self._rows('courses') if wanted in {
            normalize_alias(str(row.get(key, ''))) for key in ('Course Key', 'Code', 'Name')
        } | {normalize_alias(alias) for alias in parse_aliases(row.get('Aliases', ''))}]
        if len(matches) > 1:
            raise SourceUnavailableError('Course alias is ambiguous; use exact Course Key')
        return matches[0] if matches else None

    def _list_course(self, kind: str, course: Any) -> list[Any]:
        if isinstance(course, str):
            course = self.get_course_by_alias(course)
        if not isinstance(course, dict) or not course.get('id'):
            raise SourceUnavailableError('A resolved Course is required')
        return self._rows(kind, {'property': 'Course', 'relation': {'contains': course['id']}})

    def list_course_sessions(self, course: Any) -> list[Any]:
        return self._list_course('sessions', course)

    def list_course_materials(self, course: Any) -> list[Any]:
        return self._list_course('materials', course)

    def list_course_exams(self, course: Any) -> list[Any]:
        return self._list_course('exams', course)

    def list_course_activities(self, course: Any) -> list[Any]:
        return self._list_course('activities', course)

    def find_sessions_by_alias(self, course: Any, alias_norm: str) -> list[Any]:
        return [row for row in self.list_course_sessions(course)
                if normalize_alias(alias_norm) in {normalize_alias(a) for a in parse_aliases(row.get('Aliases', ''))}]

    def get_material_usage(self, session_id: str) -> list[Any]:
        session = self.get_session(session_id)
        if session is None:
            return []
        return self._rows('material_usage', {'property': 'Session', 'relation': {'contains': session['id']}})

    def get_session_enrichment(self, entity_id: str) -> Any:
        # No unvalidated AI body is promoted into freshness-bound enrichment.
        return None

    def get_material_enrichment(self, material_id: str) -> Any:
        return None

    def _annotations(self, row: Any) -> list[Any]:
        if row is None:
            return []
        result = self._call(self._children, block_id=row['id'], page_size=100)
        if result.get('has_more') is not False:
            raise SourcePartialError('USER region listing is incomplete')
        active = False
        refs = []
        for block in result.get('results', []):
            kind = block.get('type', '')
            text = _plain(block.get(kind, {}).get('rich_text', []))
            if kind.startswith('heading_') and text.strip().strip('[]') in {'SOURCE', 'AI', 'USER'}:
                active = text.strip().strip('[]') == 'USER'
                continue
            if active:
                if block.get('has_children'):
                    raise SourcePartialError('Nested USER content requires an expanded reader')
                if text:
                    refs.append({'source_class': 'user_source', 'entity_id': row.get('ID'),
                                 'text': text, 'content': text,
                                 'url': 'https://www.notion.so/' + row['id'].replace('-', '')
                                        + '#' + block['id'].replace('-', '')})
        return refs

    def get_session_user_annotations(self, session_id: str) -> list[Any]:
        return self._annotations(self.get_session(session_id))

    def get_material_user_annotations(self, material_id: str) -> list[Any]:
        return self._annotations(self.get_material(material_id))
