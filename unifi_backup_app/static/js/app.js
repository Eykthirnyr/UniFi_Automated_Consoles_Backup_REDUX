let evtSource = null;

function autoRemoveFlashMessages() {
  setTimeout(() => {
    const flashEls = document.querySelectorAll(".flash-message");
    flashEls.forEach((el) => el.remove());
  }, 15000);
}

function initSSE() {
  evtSource = new EventSource("/status_stream");
  evtSource.onmessage = function (e) {
    if (!e.data) return;
    const data = JSON.parse(e.data);
    updateUI(data);
  };
}

function updateUI(data) {
  const running = data.current_task.running;
  const step = data.current_task.step;
  const taskName = data.current_task.task_name || step || "";
  const startTime = data.current_task.start_time_local || "";
  const elapsedSeconds = data.current_task.elapsed_seconds;
  const queueSize = data.queue_size;
  const queueItems = data.queue_items || [];
  const scheduledQueuePosition = data.scheduled_queue_position || 0;
  const scheduledQueueSize = data.scheduled_queue_size || 0;

  const taskStatus = document.getElementById("task-status");
  const taskDetail = document.getElementById("task-detail");
  const taskSubdetail = document.getElementById("task-subdetail");
  const taskTiming = document.getElementById("task-timing");
  const queueDetail = document.getElementById("queue-detail");
  const queueList = document.getElementById("queue-list");

  if (running) {
    taskStatus.textContent = "Running";
    taskStatus.className = "badge success";
    taskDetail.textContent = taskName || "Task running";
    taskSubdetail.textContent = step && step !== taskName ? step : "Working...";
  } else if (queueSize > 0) {
    taskStatus.textContent = "Queued";
    taskStatus.className = "badge warning";
    taskDetail.textContent = "Tasks are queued and waiting.";
    taskSubdetail.textContent = "";
  } else {
    taskStatus.textContent = "Idle";
    taskStatus.className = "badge";
    taskDetail.textContent = "No task is running.";
    taskSubdetail.textContent = "";
  }

  if (running) {
    queueDetail.textContent = `Started: ${startTime || "Unknown"}`;
  } else {
    queueDetail.textContent = `Queue size: ${queueSize}`;
  }

  if (scheduledQueueSize > 0) {
    const base = queueDetail.textContent;
    queueDetail.textContent = `${base} | Scheduled queue: position ${scheduledQueuePosition}/${scheduledQueueSize}`;
  }
  if (running && elapsedSeconds !== null && elapsedSeconds !== undefined) {
    taskTiming.textContent = `Elapsed: ${elapsedSeconds}s`;
  } else {
    taskTiming.textContent = "";
  }
  queueList.innerHTML = "";
  if (queueItems.length > 0) {
    queueItems.forEach((item) => {
      const li = document.createElement("li");
      li.textContent = item;
      queueList.appendChild(li);
    });
  } else if (!running) {
    const li = document.createElement("li");
    li.className = "empty-state";
    li.textContent = "Queue is empty.";
    queueList.appendChild(li);
  }

  const nextStr = data.next_backup_time_str || "N/A";
  document.getElementById("next-backup").textContent = nextStr;
  const currentTime = data.current_time_local || "";
  const currentTimeEl = document.getElementById("current-time");
  if (currentTimeEl) {
    currentTimeEl.textContent = currentTime ? `Current time: ${currentTime}` : "";
  }

  const logsUl = document.getElementById("logs-ul");
  logsUl.innerHTML = "";
  const logsList = data.logs || [];
  if (logsList.length === 0) {
    const li = document.createElement("li");
    li.className = "empty-state";
    li.textContent = "No logs yet. Activity will appear here once tasks run.";
    logsUl.appendChild(li);
  } else {
    logsList.forEach((entry) => {
      const li = document.createElement("li");
      li.textContent = `[${entry.timestamp}] - ${entry.message}`;
      logsUl.appendChild(li);
    });
  }

  const loginStatus = data.master_logged_in;
  const loginDot = document.getElementById("login-status-dot");
  const loginText = document.getElementById("login-status-text");
  const loginCheckTime = document.getElementById("cookie-check-time");
  if (loginStatus) {
    loginDot.className = "status-dot green";
    loginText.textContent = "Cookies are valid.";
  } else {
    loginDot.className = "status-dot red";
    loginText.textContent = "Not logged in. Please do a manual server-side login.";
  }
  if (loginCheckTime) {
    const lastCheck = data.last_cookie_check_local || "";
    loginCheckTime.textContent = lastCheck ? `Last checked: ${lastCheck}` : "";
  }

  const consoles = data.consoles || [];
  const consolesKey = JSON.stringify(consoles);
  if (window.lastConsolesKey !== consolesKey) {
    window.lastConsolesKey = consolesKey;
    const consolesTbody = document.getElementById("consoles-tbody");
    consolesTbody.innerHTML = "";
    if (consoles.length === 0) {
      const row = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 6;
      td.className = "empty-state";
      td.textContent = "No consoles yet. Add one below or import a JSON list.";
      row.appendChild(td);
      consolesTbody.appendChild(row);
    } else {
      consoles.forEach((c) => {
        const row = document.createElement("tr");

      const tdName = document.createElement("td");
      tdName.textContent = c.name;
      row.appendChild(tdName);

      const tdUrl = document.createElement("td");
      tdUrl.className = "console-url";
      tdUrl.textContent = c.backup_url || "";
      row.appendChild(tdUrl);

      const tdStatus = document.createElement("td");
      tdStatus.className = "console-status";
      tdStatus.textContent = c.status || "None";
      row.appendChild(tdStatus);

      const tdTime = document.createElement("td");
      tdTime.textContent = c.time || "Never";
      row.appendChild(tdTime);

      const tdSchedule = document.createElement("td");
      tdSchedule.className = `console-schedule ${c.excluded ? "is-excluded" : "is-included"}`;
      tdSchedule.innerHTML = `
        <form method="POST" action="/toggle_console_schedule/${c.id}">
          <button type="submit" class="schedule-toggle-button">
            ${c.excluded ? "Excluded" : "Included"}
          </button>
        </form>
      `;
      row.appendChild(tdSchedule);

      const tdActions = document.createElement("td");
      tdActions.innerHTML = `
        <div class="table-actions">
          <form method="POST" action="/manual_backup/${c.id}">
            <button type="submit">Backup Now</button>
          </form>
          <form method="POST" action="/remove_console/${c.id}">
            <button type="submit" class="secondary">Remove</button>
          </form>
          <form method="GET" action="/download_latest_backup/${c.id}">
            <button type="submit" class="secondary">Download Latest</button>
          </form>
          <form method="GET" action="/console_history/${c.id}">
            <button type="submit" class="secondary">View History</button>
          </form>
        </div>
      `;
      row.appendChild(tdActions);

        consolesTbody.appendChild(row);
      });
    }
  }
}


window.addEventListener("load", () => {
  initSSE();
  autoRemoveFlashMessages();
  const backToTop = document.getElementById("back-to-top");
  if (backToTop) {
    backToTop.addEventListener("click", () => {
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }
});
