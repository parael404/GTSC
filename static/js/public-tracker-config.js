(function () {
  function readJsonScript(id, fallback) {
    const node = document.getElementById(id);
    if (!node) return fallback;

    try {
      return JSON.parse(node.textContent || '');
    } catch (error) {
      console.error(`Could not parse ${id}`, error);
      return fallback;
    }
  }

  window.liveBuses = readJsonScript('publicLiveBusesData', []);
  window.commuterData = readJsonScript('publicCommuterData', { routes: [], stopDirectory: [], stopNames: [] });
  window.liveBusEndpoint = document.body.dataset.liveBusEndpoint || '';
  window.publicCommuterEndpoint = document.body.dataset.publicCommuterEndpoint || '';
})();