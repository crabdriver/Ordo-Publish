import unittest
from pathlib import Path


class InstallPathTests(unittest.TestCase):
    def test_formula_declares_brew_dependencies_and_ordo_wrapper(self):
        formula_path = Path(__file__).resolve().parents[1] / "Formula" / "ordo.rb"
        self.assertTrue(formula_path.is_file())
        text = formula_path.read_text(encoding="utf-8")
        self.assertIn('depends_on "python@3.12"', text)
        self.assertIn('depends_on "node"', text)
        self.assertIn("venv = virtualenv_create", text)
        self.assertIn("venv.pip_install buildpath", text)
        self.assertIn('ORDO_REPO_TEMPLATE_ROOT', text)
        self.assertIn('bin.install libexec/"bin/ordo"', text)
        self.assertNotIn('(libexec/"bin/ordo").write', text)
        self.assertNotIn("tw93/mole", text)

    def test_local_installer_uses_formula_install_flow(self):
        installer_path = Path(__file__).resolve().parents[1] / "scripts" / "install_ordo.sh"
        self.assertTrue(installer_path.is_file())
        text = installer_path.read_text(encoding="utf-8")
        self.assertIn("brew install", text)
        self.assertIn("brew tap-new", text)
        self.assertIn('TAP_NAME="wizard/local"', text)
        self.assertIn('$TAP_NAME/ordo', text)
        self.assertIn("python@3.12", text)
        self.assertIn("node", text)
        self.assertIn("tar -czf", text)
        self.assertIn("venv = virtualenv_create", text)
        self.assertIn("venv.pip_install buildpath", text)
        self.assertIn('bin.install libexec/"bin/ordo"', text)
        self.assertNotIn('(libexec/"bin/ordo").write', text)
        self.assertNotIn('-m pip install "$ROOT_DIR"', text)


if __name__ == "__main__":
    unittest.main()
