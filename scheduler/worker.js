export default {
  async scheduled(controller, env, ctx) {
    const owner = env.GH_OWNER || "81dwallington-source";
    const repo = env.GH_REPO || "Forex-alert-bot";
    const workflow = env.GH_WORKFLOW_FILE || "best-bot-live.yml";
    const ref = env.GH_REF || "main";

    if (!env.GH_TOKEN) throw new Error("Missing GH_TOKEN secret");

    const response = await fetch(
      `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflow}/dispatches`,
      {
        method: "POST",
        headers: {
          "Accept": "application/vnd.github+json",
          "Authorization": `Bearer ${env.GH_TOKEN}`,
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "best-bot-five-minute-scheduler"
        },
        body: JSON.stringify({ ref })
      }
    );

    if (!response.ok) {
      const detail = await response.text();
      throw new Error(`GitHub dispatch failed: ${response.status} ${detail}`);
    }
  }
};
