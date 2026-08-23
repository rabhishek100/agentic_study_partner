from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class EnvironmentContractTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_local_runner_is_pinned_and_never_names_production(self) -> None:
        script = self.read("scripts/local.sh")
        self.assertIn('SUPABASE_VERSION="${SUPABASE_VERSION:-2.109.1}"', script)
        self.assertIn("studio,logflare,edge-runtime,vector,imgproxy", script)
        self.assertNotIn("usuulfckhbeypjxwjpfn", script)
        self.assertIn("DOCKER_SUPABASE_URL", script)

    def test_compose_uses_container_reachable_local_endpoints(self) -> None:
        compose = self.read("docker-compose.yml")
        self.assertIn("http://host.docker.internal:54321", compose)
        self.assertIn("http://127.0.0.1:54321/auth/v1", compose)
        self.assertIn("NEXT_PUBLIC_API_ORIGIN", compose)

    def test_staging_runner_is_environment_scoped(self) -> None:
        script = self.read("scripts/staging.sh")
        self.assertIn('RAILWAY_STAGING_ENVIRONMENT:-staging', script)
        self.assertIn('"$ROOT/scripts/deploy.sh" api "$ENVIRONMENT"', script)
        self.assertIn('"$ROOT/scripts/deploy.sh" web "$ENVIRONMENT"', script)

    def test_staging_auth_redirects_are_exact_and_hosted(self) -> None:
        config = self.read("ops/staging/supabase/config.toml")
        self.assertIn('site_url = "https://web-staging-f7bf.up.railway.app"', config)
        self.assertNotIn("localhost", config)
        self.assertNotIn("127.0.0.1", config)

    def test_local_generated_files_and_staging_secrets_are_ignored(self) -> None:
        gitignore = self.read(".gitignore")
        self.assertIn(".local-runtime/", gitignore)
        self.assertIn(".env.staging.local", gitignore)
        self.assertIn(".env.local", gitignore)

    def test_deferred_storage_setup_can_be_reapplied(self) -> None:
        sql = self.read("supabase/deferred/book_sources_storage.sql")
        for operation in ("select", "insert", "update", "delete"):
            policy = f'"book_sources_owner_{operation}"'
            self.assertIn(f"drop policy if exists {policy}", sql)
            self.assertIn(f"create policy {policy}", sql)


if __name__ == "__main__":
    unittest.main()
