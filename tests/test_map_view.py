import unittest


class MapViewTest(unittest.TestCase):
    def test_map_uses_compact_markers_and_clear_route_style(self) -> None:
        from src.ui import map_view

        self.assertLessEqual(map_view.MAP_MARKER_RADIUS, 7)
        self.assertGreaterEqual(map_view.MAP_ROUTE_WEIGHT, 5)
        self.assertGreaterEqual(map_view.MAP_ROUTE_OPACITY, 0.85)
        self.assertEqual(map_view.MAP_ROUTE_DASH_ARRAY, None)

    def test_map_chinese_base_layer_hides_leaflet_chrome(self) -> None:
        from src.ui import map_view

        self.assertEqual(map_view.MAP_TILE_NAME, "高德地图")
        self.assertEqual(map_view.MAP_TILE_ATTRIBUTION, "高德地图")
        self.assertFalse(map_view.MAP_SHOW_SCALE_CONTROL)
        self.assertFalse(map_view.MAP_SHOW_ATTRIBUTION_CONTROL)

    def test_map_marker_html_shows_plan_order_number(self) -> None:
        from src.ui import map_view

        html = map_view._numbered_marker_html(3, color="#2563eb")

        self.assertIn(map_view.MAP_NUMBER_MARKER_CLASS, html)
        self.assertIn(">3</div>", html)
        self.assertIn("border-radius:999px", html)
        self.assertIn("color:#ffffff", html)


if __name__ == "__main__":
    unittest.main()
