jQuery(function ($) {
  // Sidebar toggle logic for dropdown
  $(".sidebar-dropdown > a").click(function () {
    $(".sidebar-submenu").slideUp(200);
    if ($(this).parent().hasClass("active")) {
      $(".sidebar-dropdown").removeClass("active");
      $(this).parent().removeClass("active");
    } else {
      $(".sidebar-dropdown").removeClass("active");
      $(this).next(".sidebar-submenu").slideDown(200);
      $(this).parent().addClass("active");
    }
  });
});

function toggleSidebar() {
  const sidebar = document.getElementById("sidebar");
  const content = document.getElementById("content");
  sidebar.classList.toggle("collapsed");
  content.classList.toggle("collapsed");
}

// Ensure the sidebar is collapsed initially on mobile
window.addEventListener("resize", () => {
  const sidebar = document.getElementById("sidebar");
  const content = document.getElementById("content");
  if (window.innerWidth <= 992) {
    sidebar.classList.add("collapsed");
    content.classList.add("collapsed");
  } else {
    sidebar.classList.remove("collapsed");
    content.classList.remove("collapsed");
  }
});

// Trigger the resize event to set the initial state
window.dispatchEvent(new Event("resize"));


// Side bar collapsed Logo Chane
function toggleSidebar() {
  const sidebar = document.getElementById("sidebar");
  const content = document.getElementById("content");
  const logo = document.querySelector(".sidebar-brand img");

  sidebar.classList.toggle("collapsed");
  content.classList.toggle("collapsed");

  // Change logo when sidebar is collapsed
  if (sidebar.classList.contains("collapsed")) {
    logo.src = "/static/assets/images/collapsed-logo.webp"; // Change this to your collapsed logo
  } else {
    logo.src = "/static/assets/images/almabrouk-logo.webp";
  }
}


if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/static/assets/js/service-worker.js').then(reg => {
      reg.onupdatefound = () => {
          const installingWorker = reg.installing;
          installingWorker.onstatechange = () => {
              if (installingWorker.state === 'installed') {
                  if (navigator.serviceWorker.controller) {
                      console.log('New content is available; refreshing the page...');
                      window.location.reload(); // Refresh to load the latest version
                  }
              }
          };
      };
  });
}
