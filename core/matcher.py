from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class MatchResult:
    group_name: str
    matched_sheet: Optional[str] = None
    method: str = "unmatched"  # "manual", "auto", "unmatched"


class SheetMatcher:
    def __init__(
        self,
        sheet_names: List[str],
        manual_map: Optional[Dict[str, str]] = None,
    ):
        self.sheet_names = sheet_names
        self.manual_map = manual_map or {}
        self._results: Dict[str, str] = {}

    def match(self, group_names: List[str]) -> Dict[str, Optional[str]]:
        self._results = {}
        for group_name in group_names:
            self._results[group_name] = self._match_one(group_name)
        return dict(self._results)

    def _match_one(self, group_name: str) -> Optional[str]:
        # 1. Manual map takes priority
        if group_name in self.manual_map:
            sheet = self.manual_map[group_name]
            if sheet in self.sheet_names:
                return sheet

        # 2. Auto match: sheet name fully contained in group name
        #    Prefer longer sheet names first to avoid 张三 matching before 张三丰
        sorted_sheets = sorted(self.sheet_names, key=len, reverse=True)
        for sheet in sorted_sheets:
            if sheet in group_name:
                return sheet

        return None

    def get_matched_sheets(self) -> List[str]:
        return [s for s in self._results.values() if s is not None]

    def get_all_results(self) -> List[MatchResult]:
        results = []
        for group_name, sheet in self._results.items():
            method = "unmatched"
            if group_name in self.manual_map:
                method = "manual"
            elif sheet is not None:
                method = "auto"
            results.append(MatchResult(
                group_name=group_name,
                matched_sheet=sheet,
                method=method,
            ))
        return results
