"""Force stock Jumanji recursive-division mazes (one passage per wall).

The base conda ``jumanji`` install may be patched with extra chamber doors.
Call ``ensure_stock_maze_generation()`` before any ``RandomGenerator`` use.

Patches ``split_vertically`` / ``split_horizontally`` on the installed module so
``generate_maze`` keeps using the installed ``MazeGenerationState`` type.
"""

from __future__ import annotations

_PATCHED = False


def ensure_stock_maze_generation() -> None:
    """Replace installed maze splitters with stock single-passage versions."""
    global _PATCHED
    if _PATCHED:
        return

    from jumanji.environments.commons.maze_utils import maze_generation as m

    def split_vertically(state, chamber):
        x, y, width, height = chamber
        key, wall_key, passage_key = m.jax.random.split(state.key, num=3)

        wall_dy = m.random_odd(wall_key, height)
        wall_y = y + wall_dy
        maze = m.draw_horizontal_wall(state.maze, x, wall_y, width)

        chambers = m.create_chamber(state.chambers, x, y, width, wall_dy)
        chambers = m.create_chamber(chambers, x, wall_y + 1, width, height - wall_dy - 1)

        passage_x = m.random_even(passage_key, width)
        maze = maze.at[wall_y, x + passage_x].set(m.EMPTY)

        return m.MazeGenerationState(maze, chambers, key)

    def split_horizontally(state, chamber):
        x, y, width, height = chamber
        key, wall_key, passage_key = m.jax.random.split(state.key, num=3)

        wall_dx = m.random_odd(wall_key, width)
        wall_x = x + wall_dx
        maze = m.draw_vertical_wall(state.maze, wall_x, y, height)

        chambers = m.create_chamber(state.chambers, x, y, wall_dx, height)
        chambers = m.create_chamber(chambers, wall_x + 1, y, width - wall_dx - 1, height)

        passage_y = m.random_even(passage_key, height)
        maze = maze.at[y + passage_y, wall_x].set(m.EMPTY)

        return m.MazeGenerationState(maze, chambers, key)

    m.split_vertically = split_vertically
    m.split_horizontally = split_horizontally
    _PATCHED = True
