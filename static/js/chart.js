// 通用 Daily Usage 图表：柱状 / 折线切换 + 悬浮显示日期和数值
// 用法：drawChart(svg, days, values, mode)  +  initChartToggle(container, svg, days, values, labels)
(function () {
  'use strict';

  // 生成 SVG 图表。mode: 'bar' | 'line'
  window.drawChart = function (svg, days, values, mode) {
    if (!svg) return;
    if (!days || !days.length) {
      svg.innerHTML = '<text x="400" y="100" text-anchor="middle" fill="#888">No data</text>';
      return;
    }
    mode = mode || 'line';
    var max = Math.max.apply(null, values.concat([1]));
    var w = 800, h = 200, pad = 30;
    var n = days.length;
    var innerW = w - 2 * pad;
    var step = innerW / n;
    var barW = Math.max(2, Math.min(30, step * 0.7));
    var points = values.map(function (v, i) {
      return [pad + step * (i + 0.5), h - pad - (v / max) * (h - 2 * pad)];
    });
    var parts = [];

    // 横轴
    parts.push('<line x1="' + pad + '" y1="' + (h - pad) + '" x2="' + (w - pad) + '" y2="' + (h - pad) + '" stroke="#ddd"/>');

    if (mode === 'bar') {
      values.forEach(function (v, i) {
        var x = points[i][0] - barW / 2;
        var y = points[i][1];
        var bh = h - pad - y;
        parts.push('<rect class="chart-bar" data-i="' + i + '" x="' + x + '" y="' + y +
          '" width="' + barW + '" height="' + bh + '" rx="2" fill="#378add">' +
          '<title>' + days[i] + ': ' + v + '</title></rect>');
      });
    } else {
      // 折线 + 圆点
      parts.push('<polyline fill="none" stroke="#378add" stroke-width="2" points="' +
        points.map(function (p) { return p[0] + ',' + p[1]; }).join(' ') + '"/>');
      points.forEach(function (p, i) {
        parts.push('<circle class="chart-dot" data-i="' + i + '" cx="' + p[0] + '" cy="' + p[1] +
          '" r="4" fill="#378add" stroke="#fff" stroke-width="1.5">' +
          '<title>' + days[i] + ': ' + values[i] + '</title></circle>');
      });
    }

    // X 轴日期标签（最多 8 个，避免拥挤）
    var labelEvery = Math.max(1, Math.ceil(n / 8));
    days.forEach(function (d, i) {
      if (i % labelEvery === 0 || i === n - 1) {
        // X 轴只显示 MM-DD 节省空间（days 本身是完整 YYYY-MM-DD，tooltip 用完整值）
        var short = d.length >= 10 ? d.slice(5) : d;
        parts.push('<text x="' + points[i][0] + '" y="' + (h - 8) +
          '" text-anchor="middle" font-size="11" fill="#888">' + short + '</text>');
      }
    });

    svg.innerHTML = parts.join('');

    // 自定义 tooltip：悬浮在柱/点上显示「YYYY-MM-DD」和数值
    bindTooltip(svg, days, values);
  };

  function bindTooltip(svg, days, values) {
    var tip = svg.parentNode.querySelector('.chart-tooltip');
    if (!tip) {
      tip = document.createElement('div');
      tip.className = 'chart-tooltip';
      svg.parentNode.appendChild(tip);
    }
    var targets = svg.querySelectorAll('.chart-bar, .chart-dot');
    targets.forEach(function (el) {
      el.addEventListener('mousemove', function (e) {
        var i = parseInt(el.getAttribute('data-i'), 10);
        tip.innerHTML = '<b>' + days[i] + '</b><span>' + values[i] + '</span>';
        tip.style.display = 'block';
        var box = svg.parentNode.getBoundingClientRect();
        tip.style.left = (e.clientX - box.left + 12) + 'px';
        tip.style.top = (e.clientY - box.top - 10) + 'px';
      });
      el.addEventListener('mouseleave', function () { tip.style.display = 'none'; });
    });
  }

  // 初始化「柱状 | 折线」切换按钮。labels: {bar:'Bar', line:'Line'}
  window.initChartToggle = function (mount, svg, days, values, labels) {
    if (!mount) return;
    labels = labels || { bar: 'Bar', line: 'Line' };
    var mode = 'line';
    mount.innerHTML =
      '<div class="chart-toggle" role="group" aria-label="chart type">' +
      '<button type="button" class="chart-toggle-btn" data-mode="bar">' + labels.bar + '</button>' +
      '<button type="button" class="chart-toggle-btn" data-mode="line">' + labels.line + '</button>' +
      '</div>';
    var btns = mount.querySelectorAll('.chart-toggle-btn');
    function activate() {
      btns.forEach(function (b) { b.classList.toggle('active', b.getAttribute('data-mode') === mode); });
      drawChart(svg, days, values, mode);
    }
    btns.forEach(function (b) {
      b.addEventListener('click', function () { mode = b.getAttribute('data-mode'); activate(); });
    });
    activate();
  };
})();
