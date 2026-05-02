import os
from typing import List
import openpyxl

from core.task_parser import ParsedTask


class ExcelWriter:
    def __init__(self, template_path: str):
        if not os.path.exists(template_path):
            raise FileNotFoundError(f"Excel 模板不存在: {template_path}")
        self.template_path = template_path
        self._wb = openpyxl.load_workbook(template_path)

    def add_task(self, sheet_name: str, task: ParsedTask):
        if sheet_name not in self._wb.sheetnames:
            return

        ws = self._wb[sheet_name]
        target_row = self._find_or_create_date_row(ws, task)

        if target_row:
            task_text = "\n".join(f"{i+1}、{t}" for i, t in enumerate(task.tasks))
            ws.cell(row=target_row, column=2).value = task_text

    def _find_or_create_date_row(self, ws, task: ParsedTask) -> int:
        for row in range(2, ws.max_row + 1):
            cell_val = ws.cell(row=row, column=1).value
            if cell_val is not None and int(cell_val) == task.date_excel_serial:
                return row

        new_row = ws.max_row + 1
        ws.cell(row=new_row, column=1).value = task.date_excel_serial
        return new_row

    def save(self, output_path: str):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        self._wb.save(output_path)

    def close(self):
        self._wb.close()

    def get_sheet_names(self) -> List[str]:
        return self._wb.sheetnames
