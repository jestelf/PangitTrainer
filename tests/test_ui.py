import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

class LayoutTests(unittest.TestCase):
    def test_scroll_layout_small_window_and_language_switch(self):
        os.environ['LOCALAPPDATA'] = tempfile.mkdtemp(prefix='pangit-ui-tests-')
        import app
        import lang
        for language in ('ru', 'en'):
            with self.subTest(language=language):
                lang.set_language(language)
                history = app.History(Path(os.environ['LOCALAPPDATA']) / (language + '.sqlite3'))
                history.begin()
                trainer = app.PreviewTrainer({})
                view = app.Dashboard(trainer, history, {'sound_enabled': False}, hidden=True, persist=False)
                try:
                    view.root.geometry('1000x640+30000+30000')
                    view.root.deiconify()
                    view.root.update()
                    self.assertEqual(len(view.vars), 27)
                    self.assertLess(view.controls_canvas.winfo_height(), view.controls_frame.winfo_reqheight())
                    view.controls_canvas.yview_moveto(1)
                    view.root.update_idletasks()
                    self.assertGreater(view.controls_canvas.yview()[1], .99)
                    view.layout_features(SimpleNamespace(width=820))
                    view.root.update_idletasks()
                    self.assertLessEqual(max(int(card.grid_info()['column']) for card in view.feature_cards), 2)
                    actions = view.controls_scrollbar.master.grid_slaves(row=1)[0]
                    self.assertLessEqual(actions.winfo_y() + actions.winfo_height(), actions.master.winfo_height() + 1)
                    view.set_language('en' if language == 'ru' else 'ru')
                    view.root.update()
                    self.assertEqual(len(view.vars), 27)
                finally:
                    view.root.after(100, view.close)
                    view.run()

if __name__ == '__main__':
    unittest.main()
