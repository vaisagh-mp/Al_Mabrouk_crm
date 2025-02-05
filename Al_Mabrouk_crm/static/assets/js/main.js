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


