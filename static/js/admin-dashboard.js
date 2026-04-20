(function () {
    const chartDataNode = document.getElementById('adminChartData');
    const chartData = chartDataNode ? JSON.parse(chartDataNode.textContent || '{}') : {};
    const initialAdminTab = document.body.dataset.adminInitialTab || 'analytics';
    const adminLiveEndpoint = document.body.dataset.adminLiveEndpoint || '';
    const adminBurger = document.getElementById('adminBurger');
    const adminMenuOverlay = document.getElementById('adminMenuOverlay');
    const adminMenuClose = document.getElementById('adminMenuClose');
    const adminTabNav = document.getElementById('adminTabNav');
    const adminSections = document.querySelectorAll('.content-section');
    const passwordResetNotice = document.getElementById('passwordResetNotice');
    const passwordResetNoticeTitle = document.getElementById('passwordResetNoticeTitle');
    const passwordResetNoticeLatest = document.getElementById('passwordResetNoticeLatest');
    const passwordResetNoticeList = document.getElementById('passwordResetNoticeList');
    const passwordResetHeaderNotice = document.getElementById('passwordResetHeaderNotice');
    const passwordResetHeaderCount = document.getElementById('passwordResetHeaderCount');
    const passwordResetTabCount = document.getElementById('passwordResetTabCount');
    const passwordResetTabSummary = document.getElementById('passwordResetTabSummary');
    const passwordResetTabList = document.getElementById('passwordResetTabList');
    const csrfToken = document.querySelector('input[name="csrf_token"]')?.value || '';
    const reportStartDate = document.getElementById('reportStartDate');
    const reportEndDate = document.getElementById('reportEndDate');
    const operationNotificationSummary = document.getElementById('operationNotificationSummary');
    const operationNotificationList = document.getElementById('operationNotificationList');

    function markerColor(level) {
      if (level === 'High') return '#dc2626';
      if (level === 'Medium') return '#ea580c';
      return '#16a34a';
    }

    function createBusIcon(level) {
      const fill = markerColor(level);
      const svg = `
        <svg xmlns="http://www.w3.org/2000/svg" width="42" height="42" viewBox="0 0 42 42">
          <g fill="none" fill-rule="evenodd">
            <path d="M21 3C11.6 3 6 6.8 6 13v12.5c0 2.2 1.8 4 4 4H11v4.2c0 1.2 1 2.3 2.3 2.3h1.4c1.2 0 2.3-1 2.3-2.3v-4.2h8v4.2c0 1.2 1 2.3 2.3 2.3h1.4c1.2 0 2.3-1 2.3-2.3v-4.2H32c2.2 0 4-1.8 4-4V13c0-6.2-5.6-10-15-10Z" fill="${fill}" stroke="#0f172a" stroke-width="1.5"/>
            <rect x="10" y="10" width="22" height="8" rx="2.5" fill="#e0f2fe" stroke="#0f172a" stroke-width="1.2"/>
            <path d="M10 22h22" stroke="#0f172a" stroke-width="1.4"/>
            <circle cx="14" cy="28" r="3.2" fill="#111827"/>
            <circle cx="28" cy="28" r="3.2" fill="#111827"/>
          </g>
        </svg>
      `;

      return L.icon({
        iconUrl: `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`,
        iconSize: [42, 42],
        iconAnchor: [21, 21],
        popupAnchor: [0, -18]
      });
    }

    function showSection(sectionId) {
      const targetSectionId = sectionId === 'overview' ? 'analytics' : sectionId;
      adminSections.forEach((section) => {
        section.classList.toggle('is-active', section.id === targetSectionId);
      });

      document.querySelectorAll('.tab-link').forEach((button) => {
        button.classList.toggle('active', button.dataset.target === targetSectionId);
      });

      if (adminMenuOverlay) {
        adminMenuOverlay.classList.remove('open');
      }

      if (targetSectionId === 'operations') {
        setTimeout(() => {
          adminMap.invalidateSize();
          renderAdminMap();
        }, 120);
      }
    }

    new Chart(document.getElementById('dailyChart'), {
      type: 'line',
      data: {
        labels: chartData.daily_labels,
        datasets: [{
          label: 'Passengers',
          data: chartData.daily_values,
          borderColor: '#D60000',
          backgroundColor: 'rgba(214, 0, 0, 0.12)',
          fill: true,
          tension: 0.35
        }]
      },
      options: { responsive: true, maintainAspectRatio: false }
    });

    new Chart(document.getElementById('hourlyChart'), {
      type: 'bar',
      data: {
        labels: chartData.hourly_labels,
        datasets: [{
          label: 'Hourly passengers',
          data: chartData.hourly_values,
          backgroundColor: '#D60000',
          borderRadius: 10
        }]
      },
      options: { responsive: true, maintainAspectRatio: false }
    });

    const routeChartElement = document.getElementById('routeChart');
    if (routeChartElement) {
      new Chart(routeChartElement, {
        type: 'bar',
        data: {
          labels: chartData.route_labels,
          datasets: [{
            label: 'Route passengers',
            data: chartData.route_values,
            backgroundColor: ['#D60000', '#ef4444', '#f97316', '#991b1b'],
            borderRadius: 10
          }]
        },
        options: { responsive: true, maintainAspectRatio: false }
      });
    }

    new Chart(document.getElementById('mixChart'), {
      type: 'doughnut',
      data: {
        labels: chartData.mix_labels,
        datasets: [{
          data: chartData.mix_values,
          backgroundColor: ['#D60000', '#fb7185', '#f59e0b', '#374151']
        }]
      },
      options: { responsive: true, maintainAspectRatio: false }
    });

    new Chart(document.getElementById('stopChart'), {
      type: 'bar',
      data: {
        labels: chartData.stop_labels,
        datasets: [{
          label: 'Boarded passengers',
          data: chartData.stop_values,
          backgroundColor: '#2563eb',
          borderRadius: 10
        }]
      },
      options: { responsive: true, maintainAspectRatio: false }
    });

    const reportBusAnalytics = chartData.report_bus_analytics || { all: {}, buses: {}, bus_options: [] };
    const reportBusFilter = document.getElementById('reportBusFilter');
    const reportPassengerCaption = document.getElementById('reportPassengerCaption');
    const reportRevenueCaption = document.getElementById('reportRevenueCaption');

    function getReportDataset(busKey) {
      if (busKey && busKey !== 'all' && reportBusAnalytics.buses && reportBusAnalytics.buses[busKey]) {
        return reportBusAnalytics.buses[busKey];
      }
      return reportBusAnalytics.all || {};
    }

    const reportPassengerBreakdownChart = new Chart(document.getElementById('reportPassengerBreakdownChart'), {
      type: 'doughnut',
      data: {
        labels: ['Students', 'PWD', 'Senior', 'Regular'],
        datasets: [{
          data: [0, 0, 0, 0],
          backgroundColor: ['#D60000', '#fb7185', '#f59e0b', '#374151']
        }]
      },
      options: { responsive: true, maintainAspectRatio: false }
    });

    const reportRevenueBreakdownChart = new Chart(document.getElementById('reportRevenueBreakdownChart'), {
      type: 'bar',
      data: {
        labels: ['Students', 'PWD', 'Senior', 'Regular'],
        datasets: [{
          label: 'Revenue',
          data: [0, 0, 0, 0],
          backgroundColor: ['#991b1b', '#e11d48', '#ea580c', '#1f2937'],
          borderRadius: 10
        }]
      },
      options: { responsive: true, maintainAspectRatio: false }
    });

    const reportDailyPassengersChart = new Chart(document.getElementById('reportDailyPassengersChart'), {
      type: 'line',
      data: {
        labels: [],
        datasets: [{
          label: 'Passengers',
          data: [],
          borderColor: '#D60000',
          backgroundColor: 'rgba(214, 0, 0, 0.12)',
          fill: true,
          tension: 0.35
        }]
      },
      options: { responsive: true, maintainAspectRatio: false }
    });

    const reportDailyRevenueChart = new Chart(document.getElementById('reportDailyRevenueChart'), {
      type: 'bar',
      data: {
        labels: [],
        datasets: [{
          label: 'Revenue',
          data: [],
          backgroundColor: '#2563eb',
          borderRadius: 10
        }]
      },
      options: { responsive: true, maintainAspectRatio: false }
    });

    function updateReportCharts(busKey = 'all') {
      const dataset = getReportDataset(busKey);
      const passengerTotals = dataset.passenger_totals || {};
      const revenueTotals = dataset.revenue_totals || {};
      const labels = dataset.labels || [];
      const dailyPassengers = dataset.daily_passengers || [];
      const dailyRevenue = dataset.daily_revenue || [];
      const caption = busKey === 'all'
        ? 'all buses'
        : `${busKey}${dataset.route_name ? ` · ${dataset.route_name}` : ''}`;

      if (reportPassengerCaption) {
        reportPassengerCaption.textContent = `Passenger mix for ${caption}`;
      }
      if (reportRevenueCaption) {
        reportRevenueCaption.textContent = `Revenue composition for ${caption}`;
      }

      reportPassengerBreakdownChart.data.datasets[0].data = [
        passengerTotals.student || 0,
        passengerTotals.pwd || 0,
        passengerTotals.senior || 0,
        passengerTotals.regular || 0,
      ];
      reportPassengerBreakdownChart.update();

      reportRevenueBreakdownChart.data.datasets[0].data = [
        revenueTotals.student || 0,
        revenueTotals.pwd || 0,
        revenueTotals.senior || 0,
        revenueTotals.regular || 0,
      ];
      reportRevenueBreakdownChart.update();

      reportDailyPassengersChart.data.labels = labels;
      reportDailyPassengersChart.data.datasets[0].data = dailyPassengers;
      reportDailyPassengersChart.update();

      reportDailyRevenueChart.data.labels = labels;
      reportDailyRevenueChart.data.datasets[0].data = dailyRevenue;
      reportDailyRevenueChart.update();
    }

    if (reportBusFilter) {
      reportBusFilter.addEventListener('change', (event) => {
        updateReportCharts(event.target.value);
      });
    }
    updateReportCharts(reportBusFilter ? reportBusFilter.value : 'all');

    function syncReportDateLimits() {
      if (!reportStartDate || !reportEndDate) {
        return;
      }

      reportEndDate.min = reportStartDate.value || '';
      reportStartDate.max = reportEndDate.value || '';

      if (reportStartDate.value && reportEndDate.value && reportEndDate.value < reportStartDate.value) {
        reportEndDate.value = reportStartDate.value;
      }
    }

    if (reportStartDate && reportEndDate) {
      reportStartDate.addEventListener('change', syncReportDateLimits);
      reportEndDate.addEventListener('change', syncReportDateLimits);
      syncReportDateLimits();
    }

    function roundPeso(value) {
      return Math.max(Math.round(Number(value) || 0), 1);
    }

    function suggestedDiscountedFare(regularFare) {
      return roundPeso((Number(regularFare) || 0) * 0.8);
    }

    document.querySelectorAll('.route-settings-form').forEach((form) => {
      const regularInput = form.querySelector('[data-regular-fare-input]');
      const discountedInput = form.querySelector('[data-discounted-fare-input]');
      if (!regularInput || !discountedInput) {
        return;
      }

      let discountedManuallyEdited = false;

      const sanitizeDigits = (input) => {
        input.value = String(input.value || '').replace(/\D+/g, '');
      };

      const syncDiscountedFare = () => {
        sanitizeDigits(regularInput);
        if (discountedManuallyEdited) {
          return;
        }
        discountedInput.value = String(suggestedDiscountedFare(regularInput.value));
      };

      regularInput.addEventListener('input', () => {
        sanitizeDigits(regularInput);
        syncDiscountedFare();
      });
      discountedInput.addEventListener('input', () => {
        sanitizeDigits(discountedInput);
        discountedManuallyEdited = true;
      });
      regularInput.addEventListener('keydown', (event) => {
        if (['e', 'E', '+', '-', '.', ','].includes(event.key)) {
          event.preventDefault();
        }
      });
      discountedInput.addEventListener('keydown', (event) => {
        if (['e', 'E', '+', '-', '.', ','].includes(event.key)) {
          event.preventDefault();
        }
      });
      regularInput.addEventListener('change', syncDiscountedFare);
      discountedInput.addEventListener('change', () => sanitizeDigits(discountedInput));

      syncDiscountedFare();
    });

    let liveBuses = chartData.live_buses || [];
    let busCameras = chartData.bus_cameras || [];
    const cameraSlots = ['Front Road', 'Passenger Cabin', 'Rear Door'];
    const liveBusTableBody = document.getElementById('liveBusTableBody');
    let activeCameraMode = 'bus';
    const cameraSelect = document.getElementById('cameraSelect');
    const cameraBusSelect = document.getElementById('cameraBusSelect');
    const cameraModeButtons = document.querySelectorAll('.camera-mode-button');
    const cameraAllMode = document.getElementById('cameraAllMode');
    const cameraBusMode = document.getElementById('cameraBusMode');
    const cameraAllGrid = document.getElementById('cameraAllGrid');
    const cameraBusGrid = document.getElementById('cameraBusGrid');
    const cameraBusLabel = document.getElementById('cameraBusLabel');
    const cameraStatusLabel = document.getElementById('cameraStatusLabel');
    const cameraStreamLabel = document.getElementById('cameraStreamLabel');
    const cameraSeenLabel = document.getElementById('cameraSeenLabel');
    const cameraConfigForms = document.querySelectorAll('.camera-config-form');
    const adminMap = L.map('adminMap').setView([15.37, 120.94], 10);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors'
    }).addTo(adminMap);

    let adminMapLayers = [];
    let hasSetAdminMapView = false;

    function hasValidCoordinates(lat, lng) {
      return (
        lat !== null &&
        lat !== undefined &&
        lng !== null &&
        lng !== undefined &&
        Number.isFinite(Number(lat)) &&
        Number.isFinite(Number(lng)) &&
        Math.abs(Number(lat)) <= 90 &&
        Math.abs(Number(lng)) <= 180
      );
    }

    function clearAdminMapLayers() {
      adminMapLayers.forEach((layer) => adminMap.removeLayer(layer));
      adminMapLayers = [];
    }

    function addAdminLayer(layer) {
      layer.addTo(adminMap);
      adminMapLayers.push(layer);
      return layer;
    }

    function validPoint(point) {
      return Array.isArray(point) && hasValidCoordinates(point[0], point[1]);
    }

    function getStopPath(stops) {
      return stops
        .filter((stop) => hasValidCoordinates(stop.lat, stop.lng))
        .map((stop) => [Number(stop.lat), Number(stop.lng)]);
    }

    function getRoutePath(bus) {
      const stops = Array.isArray(bus.stops) ? bus.stops : [];
      const stopPath = getStopPath(stops);
      if (stopPath.length >= 2) {
        return stopPath;
      }
      return (Array.isArray(bus.coords) ? bus.coords : [])
        .filter(validPoint)
        .map((point) => [Number(point[0]), Number(point[1])]);
    }

    function renderAdminMap() {
      clearAdminMapLayers();

      if (!liveBuses.length) {
        return;
      }

      const bounds = [];

      liveBuses.forEach((bus) => {
        const routePath = getRoutePath(bus);

        if (routePath.length) {
          bounds.push(...routePath);
        }

        if (!bus.isLiveTracked || !hasValidCoordinates(bus.lat, bus.lng)) {
          return;
        }

        const marker = addAdminLayer(L.marker([Number(bus.lat), Number(bus.lng)], {
          icon: createBusIcon(bus.crowdLevel)
        }));
        marker.bindPopup(`
          <strong>${bus.id}</strong><br>
          ${bus.direction}<br>
          ${bus.nextStop}<br>
          ${bus.passengers}/${bus.capacity} passengers<br>
          ${bus.driver}
        `);
        bounds.push([Number(bus.lat), Number(bus.lng)]);
      });

      if (bounds.length && !hasSetAdminMapView) {
        adminMap.fitBounds(bounds, { padding: [32, 32], maxZoom: 14 });
        hasSetAdminMapView = true;
      }
    }

    function renderLiveBusTable(rows) {
      if (!liveBusTableBody) {
        return;
      }

      if (!rows.length) {
        liveBusTableBody.innerHTML = '<tr><td colspan="5">No active buses right now.</td></tr>';
        return;
      }

      liveBusTableBody.innerHTML = rows.map((bus) => `
        <tr>
          <td>${bus.plate_number}</td>
          <td>${bus.driver}</td>
          <td>${bus.route_name}</td>
          <td>${bus.occupancy}/${bus.capacity} · ${bus.crowd_level}</td>
          <td>${bus.location}</td>
        </tr>
      `).join('');
    }

    function selectedCamera() {
      if (!cameraSelect || !busCameras.length) {
        return null;
      }

      const selectedId = Number(cameraSelect.value);
      return busCameras.find((camera) => Number(camera.id) === selectedId) || busCameras[0];
    }

    function selectedBusPlate() {
      if (!cameraBusSelect || !cameraBusSelect.value) {
        const plates = getCameraBusPlates();
        return plates[0] || '';
      }
      return cameraBusSelect.value;
    }

    function getCameraBusPlates() {
      const cameraPlates = busCameras.map((camera) => camera.plate_number).filter(Boolean);
      const liveBusPlates = liveBuses.map((bus) => bus.id || bus.plate_number).filter(Boolean);
      return [...new Set([...cameraPlates, ...liveBusPlates])].sort();
    }

    function camerasForBus(plateNumber) {
      return busCameras.filter((camera) => camera.plate_number === plateNumber);
    }

    function cameraForSlot(cameras, slotName) {
      const keyedSlot = slotName.toLowerCase();
      return cameras.find((camera) => String(camera.camera_name || '').toLowerCase() === keyedSlot)
        || cameras.find((camera) => String(camera.camera_name || '').toLowerCase().includes(keyedSlot));
    }

    function cameraStatusDetail(camera) {
      if (!camera) {
        return 'No camera record available.';
      }

      if (camera.status === 'unconfigured') {
        return 'Add the final stream URL after camera hardware or gateway selection.';
      }

      if (camera.status !== 'online') {
        return `Camera is marked ${camera.status}. GPS tracking remains unaffected.`;
      }

      if (!camera.stream_url) {
        return 'Camera is online but no stream URL has been configured.';
      }

      return `${camera.stream_type} feed is ready.`;
    }

    function renderCameraFeedContent(container, camera, compact = false) {
      if (!container) {
        return;
      }

      if (!camera) {
        container.innerHTML = '<div class="camera-placeholder"><strong>No camera</strong><span>No camera record available.</span></div>';
        return;
      }

      const streamType = camera.stream_type || 'external';
      const status = camera.status || 'unconfigured';
      const streamUrl = camera.stream_url || '';
      const isReady = status === 'online' && streamUrl;

      if (!isReady) {
        container.innerHTML = '<div class="camera-placeholder"><strong></strong><span></span></div>';
        container.querySelector('strong').textContent = `${camera.plate_number} ${camera.camera_name}`;
        container.querySelector('span').textContent = cameraStatusDetail(camera);
        return;
      }

      if (streamType === 'mjpeg') {
        container.innerHTML = '<img class="camera-feed-media" alt="Selected bus camera feed">';
        container.querySelector('img').src = streamUrl;
        return;
      }

      if (streamType === 'embed') {
        container.innerHTML = '<iframe class="camera-feed-media" title="Selected bus camera feed" loading="lazy" allowfullscreen></iframe>';
        container.querySelector('iframe').src = streamUrl;
        return;
      }

      if (streamType === 'external' || streamType === 'webrtc' || streamType === 'rtsp_gateway') {
        container.innerHTML = '<div class="camera-placeholder"><strong></strong><span></span><a class="ghost-button camera-open-link" target="_blank" rel="noopener">Open Feed</a></div>';
        container.querySelector('strong').textContent = `${camera.plate_number} ${camera.camera_name}`;
        container.querySelector('span').textContent = compact ? `${streamType} stream configured.` : `${streamType} feed is configured as an admin-only external stream.`;
        container.querySelector('a').href = streamUrl;
        return;
      }

      container.innerHTML = '<video class="camera-feed-media" controls autoplay muted playsinline></video>';
      container.querySelector('video').src = streamUrl;
    }

    function createCameraCard(camera, slotName = '') {
      const card = document.createElement('article');
      card.className = 'camera-feed-card';

      const head = document.createElement('div');
      head.className = 'camera-feed-card-head';
      const title = document.createElement('strong');
      title.textContent = camera ? camera.camera_name : slotName;
      const status = document.createElement('span');
      status.textContent = camera ? `${camera.status} / ${camera.stream_type}` : 'not configured';
      head.append(title, status);

      const viewer = document.createElement('div');
      viewer.className = 'camera-viewer camera-card-viewer';
      if (camera) {
        renderCameraFeedContent(viewer, camera, true);
      } else {
        viewer.innerHTML = '<div class="camera-placeholder"><strong></strong><span>Camera slot is ready for setup.</span></div>';
        viewer.querySelector('strong').textContent = slotName || 'Camera slot';
      }

      card.append(head, viewer);
      return card;
    }

    function createBusCameraGroup(plateNumber, cameras) {
      const group = document.createElement('article');
      group.className = 'bus-camera-group';

      const head = document.createElement('div');
      head.className = 'bus-camera-group-head';
      const title = document.createElement('strong');
      title.textContent = plateNumber;
      const summary = document.createElement('span');
      const onlineCount = cameras.filter((camera) => camera.status === 'online').length;
      summary.textContent = `${onlineCount}/${cameraSlots.length} online`;
      head.append(title, summary);

      const slots = document.createElement('div');
      slots.className = 'bus-camera-slots';
      cameraSlots.forEach((slotName) => {
        slots.appendChild(createCameraCard(cameraForSlot(cameras, slotName), slotName));
      });

      group.append(head, slots);
      return group;
    }

    function renderBusCameraGrid(container, plateNumbers, emptyMessage) {
      if (!container) {
        return;
      }

      container.replaceChildren();
      if (!plateNumbers.length) {
        const emptyCard = document.createElement('article');
        emptyCard.className = 'bus-camera-group';
        const emptyViewer = document.createElement('div');
        emptyViewer.className = 'camera-viewer camera-card-viewer';
        emptyViewer.innerHTML = '<div class="camera-placeholder"><strong>No cameras</strong><span></span></div>';
        emptyViewer.querySelector('span').textContent = emptyMessage;
        emptyCard.appendChild(emptyViewer);
        container.appendChild(emptyCard);
        return;
      }

      plateNumbers.forEach((plateNumber) => {
        container.appendChild(createBusCameraGroup(plateNumber, camerasForBus(plateNumber)));
      });
    }

    function renderCameraMode() {
      [cameraAllMode, cameraBusMode].forEach((modeEl) => {
        if (modeEl) modeEl.classList.remove('is-active');
      });

      cameraModeButtons.forEach((button) => {
        button.classList.toggle('active', button.dataset.cameraMode === activeCameraMode);
      });

      if (activeCameraMode === 'all') {
        if (cameraAllMode) cameraAllMode.classList.add('is-active');
        renderBusCameraGrid(cameraAllGrid, getCameraBusPlates(), 'No camera records are configured yet.');
        return;
      }

      if (activeCameraMode === 'bus') {
        if (cameraBusMode) cameraBusMode.classList.add('is-active');
        const plateNumber = selectedBusPlate();
        renderBusCameraGrid(cameraBusGrid, plateNumber ? [plateNumber] : [], `No cameras are configured for ${plateNumber || 'this bus'}.`);
        syncSelectedCameraForBus(plateNumber);
        return;
      }
    }

    function renderCameraDetails(camera) {
      if (cameraBusLabel) cameraBusLabel.textContent = camera.plate_number || '--';
      if (cameraStatusLabel) cameraStatusLabel.textContent = camera.status || 'unconfigured';
      if (cameraStreamLabel) cameraStreamLabel.textContent = camera.stream_type || 'external';
      if (cameraSeenLabel) cameraSeenLabel.textContent = camera.last_seen_at || '--';

      cameraConfigForms.forEach((form) => {
        form.classList.toggle('is-active', Number(form.dataset.cameraForm) === Number(camera.id));
      });
    }

    function syncSelectedCameraForBus(plateNumber) {
      const busCameraRows = camerasForBus(plateNumber);
      const currentCamera = selectedCamera();
      const camera = busCameraRows.includes(currentCamera) ? currentCamera : busCameraRows[0];

      if (!camera) {
        if (cameraBusLabel) cameraBusLabel.textContent = plateNumber || '--';
        if (cameraStatusLabel) cameraStatusLabel.textContent = '--';
        if (cameraStreamLabel) cameraStreamLabel.textContent = '--';
        if (cameraSeenLabel) cameraSeenLabel.textContent = '--';
        cameraConfigForms.forEach((form) => form.classList.remove('is-active'));
        return;
      }

      if (cameraSelect) {
        cameraSelect.value = camera.id;
      }
      renderCameraDetails(camera);
    }

    function renderCameraOptions() {
      if (!cameraSelect) {
        return;
      }

      const currentValue = cameraSelect.value;
      const currentBusValue = cameraBusSelect ? cameraBusSelect.value : '';
      cameraSelect.replaceChildren();
      busCameras.forEach((camera) => {
        const option = document.createElement('option');
        option.value = camera.id;
        option.textContent = `${camera.plate_number} - ${camera.camera_name}`;
        cameraSelect.appendChild(option);
      });

      if (cameraBusSelect) {
        const busPlates = getCameraBusPlates();
        cameraBusSelect.replaceChildren();
        busPlates.forEach((plateNumber) => {
          const option = document.createElement('option');
          option.value = plateNumber;
          option.textContent = plateNumber;
          cameraBusSelect.appendChild(option);
        });
        if (busPlates.includes(currentBusValue)) {
          cameraBusSelect.value = currentBusValue;
        }
      }

      if (busCameras.some((camera) => String(camera.id) === currentValue)) {
        cameraSelect.value = currentValue;
      }
      renderCameraMode();
    }

    function escapeHtml(value) {
      return String(value || '').replace(/[&<>"']/g, (character) => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }[character]));
    }

    function renderPasswordResetNotice(alerts) {
      if (!passwordResetNotice || !passwordResetNoticeTitle || !passwordResetNoticeLatest || !passwordResetNoticeList) {
        return;
      }

      const rows = Array.isArray(alerts) ? alerts : [];
      if (passwordResetHeaderNotice && passwordResetHeaderCount) {
        passwordResetHeaderNotice.classList.toggle('is-hidden', rows.length === 0);
        passwordResetHeaderCount.textContent = String(rows.length);
      }
      if (passwordResetTabCount) {
        passwordResetTabCount.classList.toggle('is-hidden', rows.length === 0);
        passwordResetTabCount.textContent = String(rows.length);
      }
      if (passwordResetTabSummary) {
        passwordResetTabSummary.textContent = `${rows.length} unread request${rows.length === 1 ? '' : 's'}`;
      }
      if (passwordResetTabList) {
        passwordResetTabList.innerHTML = rows.length
          ? rows.map((alert) => `
            <article class="alert-admin-item severity-critical">
              <div>
                <strong>${escapeHtml(alert.title || 'Password reset request')}</strong>
                <span>${escapeHtml(alert.created_at)}${alert.role ? ` &middot; ${escapeHtml(alert.role)}` : ''}</span>
                <p>${escapeHtml(alert.description || 'Password reset requested')}</p>
              </div>
              <div class="notification-actions">
                <button type="button" class="mini-button" data-target="profiles">Open Profiles</button>
                <form method="POST">
                  <input type="hidden" name="csrf_token" value="${escapeHtml(csrfToken)}">
                  <input type="hidden" name="action" value="dismiss_password_reset_notification">
                  <input type="hidden" name="notification_id" value="${escapeHtml(alert.id)}">
                  <input type="hidden" name="redirect_tab" value="password-resets">
                  <button type="submit" class="notification-dismiss" aria-label="Dismiss password reset notification">&times;</button>
                </form>
              </div>
            </article>
          `).join('')
          : '<p class="section-copy">No password reset requests right now.</p>';
      }

      passwordResetNotice.classList.toggle('is-hidden', rows.length === 0);
      if (!rows.length) {
        passwordResetNoticeTitle.textContent = '0 password reset requests need admin review';
        passwordResetNoticeLatest.textContent = '';
        passwordResetNoticeList.innerHTML = '';
        return;
      }

      passwordResetNoticeTitle.textContent = `${rows.length} password reset request${rows.length === 1 ? '' : 's'} need admin review`;
      passwordResetNoticeLatest.textContent = `Latest request: ${rows[0].description || ''}`;
      passwordResetNoticeList.innerHTML = rows.map((alert) => `
        <div class="notice-pill">
          <p><strong>${escapeHtml(alert.created_at)}</strong> ${escapeHtml(alert.description || alert.title || 'Password reset requested')}</p>
          <form method="POST">
            <input type="hidden" name="csrf_token" value="${escapeHtml(csrfToken)}">
            <input type="hidden" name="action" value="dismiss_password_reset_notification">
            <input type="hidden" name="notification_id" value="${escapeHtml(alert.id)}">
            <input type="hidden" name="redirect_tab" value="${escapeHtml(initialAdminTab || 'analytics')}">
            <button type="submit" class="notification-dismiss notification-dismiss-small" aria-label="Dismiss password reset notification">&times;</button>
          </form>
        </div>
      `).join('');
    }

    function operationSeverity(type) {
      if (type === 'bus_full') return 'critical';
      if (type === 'high_crowd') return 'warning';
      return 'info';
    }

    function renderOperationNotifications(notifications) {
      if (!operationNotificationList || !operationNotificationSummary) {
        return;
      }

      const rows = Array.isArray(notifications) ? notifications : [];
      operationNotificationSummary.textContent = `${rows.length} unread event${rows.length === 1 ? '' : 's'}`;
      operationNotificationList.innerHTML = rows.length
        ? rows.map((notification) => `
          <article class="alert-admin-item severity-${operationSeverity(notification.notification_type)}">
            <div>
              <strong>${escapeHtml(notification.title || 'Operations notification')}</strong>
              <span>${escapeHtml(notification.created_at)} / ${escapeHtml(String(notification.notification_type || 'event').replaceAll('_', ' '))}</span>
              <p>${escapeHtml(notification.description || '')}</p>
            </div>
            <form method="POST">
              <input type="hidden" name="csrf_token" value="${escapeHtml(csrfToken)}">
              <input type="hidden" name="action" value="dismiss_admin_notification">
              <input type="hidden" name="notification_id" value="${escapeHtml(notification.id)}">
              <input type="hidden" name="redirect_tab" value="operations">
              <button type="submit" class="mini-button">Mark Read</button>
            </form>
          </article>
        `).join('')
        : '<p class="section-copy">No operations notifications right now.</p>';
    }

    async function refreshAdminLive() {
      try {
        const response = await fetch(adminLiveEndpoint, { cache: 'no-store' });
        if (!response.ok) {
          return;
        }
        const payload = await response.json();
        applyAdminLivePayload(payload);
      } catch (error) {
        console.error('Admin live refresh failed', error);
      }
    }

    function applyAdminLivePayload(payload) {
      liveBuses = Array.isArray(payload.live_buses) ? payload.live_buses : [];
      busCameras = Array.isArray(payload.bus_cameras) ? payload.bus_cameras : busCameras;
      renderLiveBusTable(Array.isArray(payload.live_bus_rows) ? payload.live_bus_rows : []);
      renderCameraOptions();
      renderAdminMap();
      renderPasswordResetNotice(payload.password_reset_alerts);
      renderOperationNotifications(payload.operation_notifications);
    }

    function connectAdminLiveSocket() {
      if (typeof io !== 'function') {
        return;
      }

      const socket = io({
        transports: ['polling'],
        reconnection: true
      });

      socket.on('admin_live:update', applyAdminLivePayload);
    }

    if (adminBurger && adminMenuOverlay) {
      adminBurger.addEventListener('click', () => adminMenuOverlay.classList.add('open'));
    }

    if (adminMenuClose && adminMenuOverlay) {
      adminMenuClose.addEventListener('click', () => adminMenuOverlay.classList.remove('open'));
      adminMenuOverlay.addEventListener('click', (event) => {
        if (event.target === adminMenuOverlay) {
          adminMenuOverlay.classList.remove('open');
        }
      });
    }

    if (adminTabNav) {
      adminTabNav.addEventListener('click', (event) => {
        const button = event.target.closest('.tab-link');
        if (!button) {
          return;
        }
        showSection(button.dataset.target);
      });
    }

    document.querySelectorAll('.notice-action[data-target]').forEach((button) => {
      button.addEventListener('click', () => showSection(button.dataset.target));
    });

    if (passwordResetTabList) {
      passwordResetTabList.addEventListener('click', (event) => {
        const button = event.target.closest('[data-target]');
        if (button) {
          showSection(button.dataset.target);
        }
      });
    }

    if (passwordResetHeaderNotice) {
      passwordResetHeaderNotice.addEventListener('click', () => {
        showSection(passwordResetHeaderNotice.dataset.target || 'profiles');
        if (passwordResetNotice) {
          passwordResetNotice.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
      });
    }

    if (cameraSelect) {
      cameraSelect.addEventListener('change', () => {
        const camera = selectedCamera();
        if (camera && cameraBusSelect) {
          cameraBusSelect.value = camera.plate_number;
        }
        activeCameraMode = 'bus';
        renderCameraDetails(camera);
        renderCameraMode();
      });
    }

    if (cameraBusSelect) {
      cameraBusSelect.addEventListener('change', () => {
        activeCameraMode = 'bus';
        renderCameraMode();
      });
    }

    cameraModeButtons.forEach((button) => {
      button.addEventListener('click', () => {
        activeCameraMode = button.dataset.cameraMode || 'bus';
        renderCameraMode();
      });
    });

    if (cameraSelect) {
      renderCameraOptions();
    }

    showSection(initialAdminTab || 'analytics');
    connectAdminLiveSocket();
    setInterval(refreshAdminLive, 5000);
})();
