(function () {
    const activeTripData = document.getElementById('driverActiveTripData');
    const activeTrip = activeTripData ? JSON.parse(activeTripData.textContent || 'null') : null;
    const driverEndpoints = document.body.dataset;
    const csrfHeaders = driverEndpoints.csrfToken ? { 'X-CSRFToken': driverEndpoints.csrfToken } : {};
    const startTripForm = document.getElementById('startTripForm');
    const endTripBtn = document.getElementById('endTripBtn');
    const trackingBtn = document.getElementById('trackingBtn');
    const trackingStatus = document.getElementById('trackingStatus');
    const gpsState = document.getElementById('gpsState');
    const gpsDetail = document.getElementById('gpsDetail');
    const currentStopValue = document.getElementById('currentStopValue');
    const currentStopDetail = document.getElementById('currentStopDetail');
    const LOCATION_REFRESH_MS = 3000;
    const MIN_LOCATION_SEND_MS = 2500;
    const GEO_OPTIONS = {
      enableHighAccuracy: true,
      maximumAge: 1000,
      timeout: 5000
    };
    let watchId = null;
    let locationPollId = null;
    let locationPushInFlight = false;
    let lastLocationSentAt = 0;

    function parseServerTime(value) {
      if (!value) {
        return null;
      }
      const normalized = String(value).trim().replace(' ', 'T');
      const date = new Date(`${normalized}Z`);
      return Number.isNaN(date.getTime()) ? null : date;
    }

    function formatServerTime(value) {
      const date = parseServerTime(value);
      return date ? date.toLocaleString() : value;
    }

    document.querySelectorAll('[data-local-time]').forEach((node) => {
      node.textContent = formatServerTime(node.dataset.localTime || node.textContent);
    });

    if (startTripForm) {
      startTripForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const formData = new FormData(startTripForm);
        const response = await fetch(driverEndpoints.startTripUrl, {
          method: 'POST',
          headers: csrfHeaders,
          body: formData
        });
        const result = await response.json();
        if (result.success) {
          sessionStorage.setItem('codexmbs_auto_track', '1');
          window.location.reload();
          return;
        }
        alert(result.error || 'Could not start trip.');
      });
    }

    if (endTripBtn) {
      endTripBtn.addEventListener('click', async () => {
        if (!window.confirm('Are you sure you want to end this trip?')) {
          return;
        }
        if (watchId !== null && navigator.geolocation) {
          navigator.geolocation.clearWatch(watchId);
          watchId = null;
        }
        if (locationPollId !== null) {
          clearInterval(locationPollId);
          locationPollId = null;
        }
        try {
          const response = await fetch(driverEndpoints.endTripUrl, { method: 'POST', headers: csrfHeaders });
          const result = await response.json().catch(() => ({}));
          if (response.ok && result.success) {
            window.location.reload();
            return;
          }
          alert(result.error || `Could not end trip. Server returned ${response.status}.`);
        } catch (error) {
          alert('Could not end trip. Check your connection and try again.');
        }
      });
    }

    if (activeTrip && activeTrip.started_at) {
      const startTime = parseServerTime(activeTrip.started_at);
      const durationNode = document.getElementById('tripDuration');
      const updateDuration = () => {
        if (!startTime) {
          return;
        }
        const elapsed = Math.max(0, Date.now() - startTime.getTime());
        const hours = String(Math.floor(elapsed / 3600000)).padStart(2, '0');
        const minutes = String(Math.floor((elapsed % 3600000) / 60000)).padStart(2, '0');
        const seconds = String(Math.floor((elapsed % 60000) / 1000)).padStart(2, '0');
        if (durationNode) {
          durationNode.textContent = `${hours}:${minutes}:${seconds}`;
        }
      };
      updateDuration();
      setInterval(updateDuration, 1000);
    }

    async function pushLocation(latitude, longitude) {
      const response = await fetch(driverEndpoints.driverLocationUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...csrfHeaders
        },
        body: JSON.stringify({ latitude, longitude })
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        return { success: false, error: payload.error || `GPS server rejected the update (${response.status}).` };
      }
      return payload;
    }

    function setTrackingStatus(message, isError = false) {
      if (!trackingStatus) return;
      trackingStatus.textContent = message;
      trackingStatus.classList.toggle('error', isError);
      trackingStatus.classList.toggle('live', !isError && message.toLowerCase().includes('active'));
      if (gpsState) {
        gpsState.textContent = isError ? 'GPS needs attention' : 'GPS running';
      }
    }

    function locationErrorMessage(error) {
      if (!error) {
        return 'Location permission denied or unavailable.';
      }
      if (error.code === error.PERMISSION_DENIED) {
        return 'Location permission is blocked. Allow location for this site, then retry GPS.';
      }
      if (error.code === error.POSITION_UNAVAILABLE) {
        return 'GPS position is unavailable. Check device location settings and signal.';
      }
      if (error.code === error.TIMEOUT) {
        return 'GPS timed out. Move near a window or outside, then retry GPS.';
      }
      return error.message || 'Location permission denied or unavailable.';
    }

    async function handlePosition(position) {
      const now = Date.now();
      if (locationPushInFlight || now - lastLocationSentAt < MIN_LOCATION_SEND_MS) {
        return;
      }

      locationPushInFlight = true;
      try {
        const result = await pushLocation(position.coords.latitude, position.coords.longitude);
        if (result.success) {
          lastLocationSentAt = Date.now();
          const sentAt = new Date().toLocaleTimeString();
          setTrackingStatus(`GPS active. Last update: ${sentAt}`);
          if (gpsDetail) {
            gpsDetail.textContent = `Last GPS log: ${sentAt}`;
          }
          if (currentStopValue && result.current_stop) {
            currentStopValue.textContent = result.current_stop;
          }
          if (currentStopDetail && Number.isFinite(Number(result.latitude)) && Number.isFinite(Number(result.longitude))) {
            currentStopDetail.textContent = `${Number(result.latitude).toFixed(6)}, ${Number(result.longitude).toFixed(6)}`;
          }
        } else {
          setTrackingStatus(result.error || 'Location update failed.', true);
        }
      } catch (error) {
        setTrackingStatus('GPS update failed. Check server connection.', true);
      } finally {
        locationPushInFlight = false;
      }
    }

    function stopTrackingWatch() {
      if (watchId !== null && navigator.geolocation) {
        navigator.geolocation.clearWatch(watchId);
        watchId = null;
      }
      if (locationPollId !== null) {
        clearInterval(locationPollId);
        locationPollId = null;
      }
    }

    function handleLocationError(error) {
      stopTrackingWatch();
      setTrackingStatus(locationErrorMessage(error), true);
      if (trackingBtn) {
        trackingBtn.textContent = 'Retry GPS';
        trackingBtn.disabled = false;
      }
    }

    function requestCurrentPosition() {
      navigator.geolocation.getCurrentPosition(handlePosition, handleLocationError, GEO_OPTIONS);
    }

    function startTracking() {
      if (!activeTrip || !navigator.geolocation) {
        setTrackingStatus('Geolocation is not supported on this device.', true);
        return;
      }

      if (watchId !== null) {
        setTrackingStatus('GPS transmission is already running.');
        return;
      }

      setTrackingStatus('Waiting for current location...');
      if (trackingBtn) {
        trackingBtn.textContent = 'GPS Locked On';
        trackingBtn.disabled = true;
      }

      watchId = navigator.geolocation.watchPosition(
        handlePosition,
        handleLocationError,
        GEO_OPTIONS
      );
      requestCurrentPosition();
      locationPollId = setInterval(requestCurrentPosition, LOCATION_REFRESH_MS);
    }

    if (!activeTrip && gpsState) {
      gpsState.textContent = 'Waiting for trip start';
    }

    if (activeTrip) {
      sessionStorage.removeItem('codexmbs_auto_track');
      startTracking();
    }

    if (trackingBtn) {
      trackingBtn.addEventListener('click', startTracking);
    }
})();
