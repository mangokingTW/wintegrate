"""Typed views over grid and tree controls.

These are thin wrappers, not a control hierarchy: each one holds a `UiaElement`
and adds the operations that only make sense for that shape of control. The
underlying element stays reachable as `.element`, so nothing here is a dead end —
anything the wrapper does not cover is still one attribute away.

They exist because grids and trees are where "verified action" stops being
obvious. Selecting a row you cannot see, or expanding a node whose children have
not been created yet, both fail in ways that look like success: the call returns,
and the state you asked for never arrives. Every mutating method here asserts its
post-condition, and every lookup deals with virtualization before it decides an
item is missing.
"""

from __future__ import annotations

import time

from wintegrate.element import (
    UIA_GridItemPatternId,
    UIA_GridPatternId,
    UIA_HeaderItemControlTypeId,
    UIA_TableItemPatternId,
    UIA_TablePatternId,
    UIA_TreeItemControlTypeId,
    UiaElement,
)
from wintegrate.exceptions import ActionVerificationError, ElementNotFoundError

# UIA_NamePropertyId, for ItemContainerPattern lookups.
UIA_NamePropertyId = 30005


class DataGridCell:
    """One cell of a grid, with its position and the row/column headers around it."""

    def __init__(self, element: UiaElement):
        self.element = element

    def __repr__(self) -> str:
        return f"DataGridCell(row={self.row}, column={self.column}, value={self.value!r})"

    @property
    def value(self) -> str:
        """The cell's text, from its value or, failing that, its name."""
        text = self.element.get_value()
        return text if text else self.element.name

    def _grid_item(self):
        return self.element._pattern(UIA_GridItemPatternId, "IUIAutomationGridItemPattern")

    @property
    def row(self) -> int:
        pat = self._grid_item()
        return int(pat.CurrentRow) if pat else -1

    @property
    def column(self) -> int:
        pat = self._grid_item()
        return int(pat.CurrentColumn) if pat else -1

    @property
    def column_header(self) -> str:
        """The header of this cell's column, or "" when the grid exposes none."""
        pat = self.element._pattern(UIA_TableItemPatternId, "IUIAutomationTableItemPattern")
        if pat is None:
            return ""
        try:
            headers = pat.GetCurrentColumnHeaderItems()
            if headers and headers.Length:
                return UiaElement(headers.GetElement(0)).name
        except Exception:
            pass
        return ""

    def select_verified(self, timeout: float = 2.0) -> bool:
        """Selects this cell and confirms the selection stuck. Scrolls to it first."""
        self.element.ensure_available()
        return self.element.select_verified(timeout=timeout)

    def invoke(self) -> bool:
        self.element.ensure_available()
        return self.element.invoke()


class DataGridRow:
    """One row of a grid, addressed by index and read by column."""

    def __init__(self, grid: DataGrid, index: int):
        self.grid = grid
        self.index = index

    def __repr__(self) -> str:
        return f"DataGridRow(index={self.index})"

    def cell(self, column: int | str) -> DataGridCell:
        return self.grid.get_cell(self.index, column)

    def values(self) -> list[str]:
        """Every cell in this row, left to right."""
        return [self.cell(c).value for c in range(self.grid.column_count)]

    def select_verified(self, timeout: float = 2.0) -> bool:
        """Selects the row by selecting its first cell, which is how a grid row is reached."""
        return self.cell(0).select_verified(timeout=timeout)


class DataGrid:
    """
    A grid or table: `SysListView32` in report mode, a WPF `DataGrid`, a WinUI
    `ItemsRepeater` in grid form — anything whose UIA provider exposes GridPattern.

    Cell lookup goes through `GridPattern.GetItem`, which addresses the *data*
    rather than the visual tree, so it reaches rows that are scrolled out of view.
    Rows that virtualization has not materialised are realised on access.
    """

    def __init__(self, element: UiaElement):
        self.element = element
        if self._grid() is None:
            raise ActionVerificationError(
                f"Element does not support GridPattern, so it is not a grid: {element.describe()}"
            )

    def __repr__(self) -> str:
        return f"DataGrid(rows={self.row_count}, columns={self.column_count})"

    def _grid(self):
        return self.element._pattern(UIA_GridPatternId, "IUIAutomationGridPattern")

    def _table(self):
        return self.element._pattern(UIA_TablePatternId, "IUIAutomationTablePattern")

    @property
    def row_count(self) -> int:
        pat = self._grid()
        return int(pat.CurrentRowCount) if pat else 0

    @property
    def column_count(self) -> int:
        pat = self._grid()
        return int(pat.CurrentColumnCount) if pat else 0

    def get_column_headers(self) -> list[str]:
        """
        Column header texts, left to right. Empty when the provider exposes none.

        TablePattern is asked first, then the header elements directly: a WPF
        DataGrid supports TablePattern but answers GetCurrentColumnHeaders with an
        empty collection, keeping the header texts on HeaderItem children instead.
        Trusting the pattern alone reports a grid with no headers, which reads like
        the grid has none rather than like the query looked in the wrong place.
        """
        pat = self._table()
        if pat is not None:
            try:
                headers = pat.GetCurrentColumnHeaders()
                if headers and headers.Length:
                    return [UiaElement(headers.GetElement(i)).name for i in range(headers.Length)]
            except Exception:
                pass

        names = [
            item.name
            for item in self.element.find_all(control_type_id=UIA_HeaderItemControlTypeId)
            if item.name
        ]
        # Providers pad the header row with unnamed spacers; a grid never has more
        # headers than columns.
        return names[: self.column_count]

    def column_index(self, column: int | str) -> int:
        """Resolves a column given either its index or its header text."""
        if isinstance(column, int):
            return column
        headers = self.get_column_headers()
        try:
            return headers.index(column)
        except ValueError:
            raise ElementNotFoundError(
                f"No column headed {column!r}; headers are {headers}"
            ) from None

    def get_cell(self, row: int, column: int | str) -> DataGridCell:
        """
        Returns the cell at (row, column), scrolling and realising it as needed.

        `column` may be an index or a header string. Raises `ElementNotFoundError`
        rather than returning something empty when the coordinates are outside the
        grid — an out-of-range cell is a caller mistake, not a missing element.
        """
        col = self.column_index(column)
        rows, cols = self.row_count, self.column_count
        if not (0 <= row < rows) or not (0 <= col < cols):
            raise ElementNotFoundError(f"Cell ({row}, {col}) is outside a {rows}x{cols} grid")

        pat = self._grid()
        try:
            found = pat.GetItem(row, col)
        except Exception as exc:
            raise ElementNotFoundError(f"GetItem({row}, {col}) failed: {exc}") from exc
        if not found:
            raise ElementNotFoundError(f"Grid has no cell at ({row}, {col})")

        cell = DataGridCell(UiaElement(found))
        cell.element.ensure_available()
        return cell

    def row(self, index: int) -> DataGridRow:
        return DataGridRow(self, index)

    def rows(self) -> list[DataGridRow]:
        return [DataGridRow(self, i) for i in range(self.row_count)]

    def find_row_by_cell_value(self, column: int | str, expected: str) -> DataGridRow:
        """
        Finds the first row whose cell in `column` equals `expected`.

        Walks the grid through GridPattern rather than the visual tree, so a match
        in a row that has never been scrolled to is still found.
        """
        col = self.column_index(column)
        for index in range(self.row_count):
            if self.get_cell(index, col).value == expected:
                return DataGridRow(self, index)
        raise ElementNotFoundError(
            f"No row where column {column!r} equals {expected!r} (searched {self.row_count} rows)"
        )

    def select_cell_verified(self, row: int, column: int | str, timeout: float = 2.0) -> bool:
        """Selects one cell and confirms it became selected."""
        return self.get_cell(row, column).select_verified(timeout=timeout)


class TreeViewItem:
    """One node of a tree, with the expansion and selection its shape implies."""

    def __init__(self, element: UiaElement):
        self.element = element

    def __repr__(self) -> str:
        return f"TreeViewItem({self.name!r})"

    @property
    def name(self) -> str:
        return self.element.name

    @property
    def is_expanded(self) -> bool:
        return self.element.expand_collapse_state == 1

    @property
    def is_leaf(self) -> bool:
        """True when the provider says this node has nothing to expand."""
        return self.element.expand_collapse_state == 3

    def scroll_into_view(self) -> bool:
        return self.element.scroll_into_view()

    def select_verified(self, timeout: float = 2.0) -> bool:
        self.element.ensure_available()
        return self.element.select_verified(timeout=timeout)

    def expand_verified(self, timeout: float = 2.0) -> bool:
        """Expands this node and confirms it. A leaf is already 'expanded enough'."""
        if self.is_leaf:
            return True
        self.element.ensure_available()
        return self.element.expand_verified(True, timeout=timeout)

    def collapse_verified(self, timeout: float = 2.0) -> bool:
        if self.is_leaf:
            return True
        return self.element.expand_verified(False, timeout=timeout)

    def children_items(self, timeout: float = 2.0) -> list[TreeViewItem]:
        """
        This node's child tree items.

        Expanding a node does not mean its children exist yet: a virtualizing tree
        creates them asynchronously, so an immediate read returns an empty list
        that looks like "no children". This waits for them to appear, and only
        concludes there are none once the node reports itself as a leaf or the
        timeout passes.
        """
        deadline = time.monotonic() + timeout
        while True:
            items = [
                TreeViewItem(child)
                for child in self.element.find_all(control_type_id=UIA_TreeItemControlTypeId)
            ]
            if items or self.is_leaf or time.monotonic() >= deadline:
                return items
            time.sleep(0.1)

    def find_child(self, name: str, timeout: float = 2.0) -> TreeViewItem | None:
        """
        Finds a direct child by name, falling back to ItemContainerPattern.

        The fallback matters for a virtualized tree: a child that has not been
        created has no element in the tree to match, but the container still knows
        about it and can produce it on request.
        """
        for child in self.children_items(timeout=timeout):
            if child.name == name:
                return child

        found = self.element.find_item_by_property(UIA_NamePropertyId, name)
        if found is not None:
            found.ensure_available()
            return TreeViewItem(found)
        return None


class TreeView:
    """
    A hierarchical tree: `SysTreeView32`, a WPF `TreeView`, or anything exposing
    TreeItem children.
    """

    def __init__(self, element: UiaElement):
        self.element = element

    def __repr__(self) -> str:
        return f"TreeView({self.element.name!r})"

    @property
    def root_items(self) -> list[TreeViewItem]:
        return [
            TreeViewItem(child)
            for child in self.element.find_all(control_type_id=UIA_TreeItemControlTypeId)
        ]

    def find_root(self, name: str) -> TreeViewItem | None:
        for item in self.root_items:
            if item.name == name:
                return item
        return None

    def navigate_path_verified(
        self,
        path: str | list[str],
        separator: str = "/",
        timeout: float = 5.0,
    ) -> TreeViewItem:
        """
        Walks a path from the roots to a node, expanding each ancestor and
        selecting the destination.

        Every step is verified rather than assumed: each ancestor's expansion is
        confirmed before its children are read, and the read waits for virtualized
        children to materialise. Without that, a tree that is merely slow looks
        exactly like a tree that does not contain the path, and the failure names
        the wrong node.

        Raises `ElementNotFoundError` naming the segment that could not be found
        and the names that were available there, since "not found" without the
        alternatives is the least useful failure a navigator can produce.
        """
        segments = path.split(separator) if isinstance(path, str) else list(path)
        if not segments:
            raise ValueError("navigate_path_verified needs at least one path segment")

        current: TreeViewItem | None = None
        for depth, segment in enumerate(segments):
            if current is None:
                candidates = self.root_items
                current = self.find_root(segment)
            else:
                if not current.expand_verified(timeout=timeout):
                    raise ActionVerificationError(
                        f"Could not expand {separator.join(segments[:depth])!r} "
                        f"while navigating to {separator.join(segments)!r}"
                    )
                candidates = current.children_items(timeout=timeout)
                current = current.find_child(segment, timeout=timeout)

            if current is None:
                available = [c.name for c in candidates]
                where = separator.join(segments[:depth]) or "<root>"
                raise ElementNotFoundError(
                    f"No tree item named {segment!r} under {where!r}; available: {available}"
                )

        current.select_verified(timeout=timeout)
        return current
